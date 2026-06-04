"""
StubHub Knicks Ticket Price Scraper

Scrapes section-level and listing-level ticket prices for all upcoming
Knicks games. StubHub embeds a JSON blob in event page HTML containing:
  - grid.venueMapData.sectionPopupData: min price per section (all sections)
  - grid.items[]: individual listing details (first page)
  - grid metadata: overall min/max, total listings, ticket classes

Requires: playwright, playwright-stealth
"""

import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

PERFORMER_URL = "https://www.stubhub.com/new-york-knicks-tickets/performer/2742"
DATA_DIR = Path(__file__).parent / "data"
SECTIONS_CSV = DATA_DIR / "section_prices.csv"
LISTINGS_CSV = DATA_DIR / "listings.csv"
VIEWERS_CSV = DATA_DIR / "viewers.csv"
LATEST_JSON = DATA_DIR / "latest.json"

SECTION_FIELDS = [
    "scraped_at", "event_id", "event_name", "event_date", "venue",
    "section_key", "section_name", "ticket_class", "ticket_class_name",
    "min_price", "listing_count", "ticket_count", "row",
]

LISTING_FIELDS = [
    "scraped_at", "event_id", "event_name", "event_date", "venue",
    "listing_id", "section", "section_id", "ticket_class_name",
    "row", "seat_from", "seat_to", "raw_price", "formatted_price",
    "available_tickets", "ticket_type",
]


def create_browser():
    """Create a stealth Playwright browser context."""
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    stealth = Stealth()
    return p, browser, context, stealth


def get_event_urls(page):
    """Load the Knicks performer page and extract event URLs + viewer count.

    Reuses the provided page so WAF tokens carry over to event page loads.
    Returns (events_list, viewer_count).
    """
    print(f"Loading performer page: {PERFORMER_URL}")
    page.goto(PERFORMER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(15)  # Wait for WAF challenge + content render

    title = page.title()
    print(f"  Title: {title}")

    # Extract viewer count ("X people viewed ... in the past hour")
    body_text = page.inner_text("body")
    viewer_count = 0
    viewer_match = re.search(r"([\d,]+)\s+people viewed", body_text)
    if viewer_match:
        viewer_count = int(viewer_match.group(1).replace(",", ""))
        print(f"  Viewers: {viewer_count:,} people in the past hour")

    # Extract event links with text
    raw_links = page.eval_on_selector_all(
        'a[href*="/event/"]',
        """els => els.map(e => ({
            href: e.href,
            text: e.innerText.replace(/\\n/g, ' | ').substring(0, 300)
        }))"""
    )

    # Deduplicate and filter to Knicks events
    seen = set()
    events = []
    for link in raw_links:
        url = link["href"].split("?")[0]  # Strip query params
        if url in seen:
            continue
        seen.add(url)
        text = link["text"].strip()
        # Only keep links with event text (Knicks-related)
        if text and ("knick" in text.lower() or "nyk" in text.lower()
                      or "spurs" in text.lower() or "nba" in text.lower()
                      or "game" in text.lower()):
            events.append({"url": url, "text": text})

    print(f"  Found {len(events)} Knicks events")
    return events, viewer_count


def extract_event_data(html):
    """Parse the embedded JSON from an event page's HTML.

    StubHub embeds a large JSON blob in a <script> tag containing
    the viagogo-event app state with all listing and section data.
    """
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)

    event_data = None
    event_meta = None

    for script in scripts:
        script = script.strip()
        if not script.startswith("{"):
            continue
        try:
            data = json.loads(script)
        except (json.JSONDecodeError, ValueError):
            continue

        # The main listing data blob has appName=viagogo-event and a grid
        if isinstance(data, dict) and data.get("appName") == "viagogo-event" and "grid" in data:
            event_data = data

        # JSON-LD event schema has event name, date, venue
        if isinstance(data, dict) and data.get("@type") in ("Event", "SportsEvent"):
            event_meta = data

    return event_data, event_meta


def wait_for_full_page(page, timeout_s=45):
    """Wait until the page has passed the WAF challenge and fully rendered.

    StubHub's AWS WAF serves a small challenge page (~2.5K) that runs JS,
    obtains a token, then calls window.location.reload(). We need to wait
    for that reload to finish and the real page (>50K) to arrive.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        html = page.content()
        if len(html) > 50000:
            return html
        time.sleep(3)
    return page.content()


def scrape_event(page, event_url, max_retries=2):
    """Navigate an existing page to an event URL and extract pricing data.

    The listing JSON blob is in the raw server response HTML but gets removed
    from the DOM after React hydration. We intercept the HTTP response to
    capture it before JavaScript consumes and removes it.
    """
    for attempt in range(max_retries + 1):
        # Intercept the raw HTML response for this URL
        captured_html = {}

        def on_response(response):
            if ("/event/" in response.url
                    and "text/html" in response.headers.get("content-type", "")
                    and response.status == 200):
                try:
                    body = response.text()
                    if len(body) > 100000:  # Real page, not WAF shell
                        captured_html["raw"] = body
                except Exception:
                    pass

        page.on("response", on_response)
        try:
            print(f"  Loading: {event_url}" + (f" (retry {attempt})" if attempt else ""))
            page.goto(event_url, wait_until="domcontentloaded", timeout=60000)
            wait_for_full_page(page)

            if captured_html.get("raw"):
                break

            # If no raw capture yet, WAF might still be resolving
            print(f"    No raw HTML captured, waiting longer...")
            time.sleep(10)
        except Exception as e:
            print(f"    Navigation error: {e}")
            if attempt < max_retries:
                time.sleep(5)
        finally:
            page.remove_listener("response", on_response)

        if captured_html.get("raw"):
            break

    html = captured_html.get("raw", "")
    if not html:
        print(f"    WARNING: Failed to capture raw HTML response")
        return None

    event_data, event_meta = extract_event_data(html)
    if not event_data:
        print(f"    WARNING: No listing data in {len(html):,} chars HTML")
        return None

    grid = event_data["grid"]

    # Extract event metadata
    event_id = grid.get("eventId", "")
    event_name = ""
    event_date = ""
    venue = ""
    if event_meta:
        event_name = event_meta.get("name", "")
        event_date = event_meta.get("startDate", "")
        location = event_meta.get("location", {})
        if isinstance(location, dict):
            venue = location.get("name", "")

    # Build ticket class name lookup
    tc_names = grid.get("availableTicketClassPairs", {})

    # --- Section-level data ---
    section_popup = grid.get("venueMapData", {}).get("sectionPopupData", {})
    sections = []
    for key, sec in section_popup.items():
        # key format: "ticketClassId_sectionId"
        parts = key.split("_", 1)
        tc_id = parts[0] if len(parts) > 1 else ""
        section_name = ""

        # Look up section name from venue configuration
        venue_config = event_data.get("venueConfiguration", {})
        if key in venue_config:
            section_name = venue_config[key].get("sectionName", "")
        elif not section_name:
            # Try to derive from the key
            for vc_key, vc_val in venue_config.items():
                if vc_key == key:
                    section_name = vc_val.get("sectionName", "")
                    break

        sections.append({
            "event_id": event_id,
            "event_name": event_name,
            "event_date": event_date,
            "venue": venue,
            "section_key": key,
            "section_name": section_name,
            "ticket_class": tc_id,
            "ticket_class_name": tc_names.get(tc_id, ""),
            "min_price": sec.get("rawMinPrice", ""),
            "listing_count": sec.get("count", 0),
            "ticket_count": sec.get("ticketCount", 0),
            "row": sec.get("rowText", ""),
        })

    # --- Individual listings ---
    listings = []
    for item in grid.get("items", []):
        listings.append({
            "event_id": event_id,
            "event_name": event_name,
            "event_date": event_date,
            "venue": venue,
            "listing_id": item.get("listingId", item.get("id", "")),
            "section": item.get("section", ""),
            "section_id": item.get("sectionId", ""),
            "ticket_class_name": item.get("ticketClassName", ""),
            "row": item.get("row", ""),
            "seat_from": item.get("seatFrom", ""),
            "seat_to": item.get("seatTo", ""),
            "raw_price": item.get("rawPrice", ""),
            "formatted_price": item.get("price", ""),
            "available_tickets": item.get("availableTickets", ""),
            "ticket_type": item.get("ticketTypeName", ""),
        })

    result = {
        "event_id": event_id,
        "event_name": event_name,
        "event_date": event_date,
        "venue": venue,
        "event_url": event_url,
        "min_price": grid.get("minPrice", ""),
        "max_price": grid.get("maxPrice", ""),
        "total_listings": grid.get("totalCount", grid.get("totalFilteredListings", 0)),
        "ticket_classes": tc_names,
        "sections": sections,
        "listings": listings,
    }

    print(f"    {event_name}")
    print(f"    {len(sections)} sections, {len(listings)} listings, "
          f"${grid.get('minPrice', '?'):,.0f} - ${grid.get('maxPrice', '?'):,.0f}")

    return result


def save_viewers(viewer_count):
    """Append viewer count to viewers CSV."""
    DATA_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    file_exists = VIEWERS_CSV.exists()
    with open(VIEWERS_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["scraped_at", "viewers_past_hour"])
        if not file_exists:
            writer.writeheader()
        writer.writerow({"scraped_at": now, "viewers_past_hour": viewer_count})


def save_data(all_events):
    """Save scraped data to CSVs and JSON snapshot."""
    DATA_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # --- Section prices CSV (append) ---
    sections_exist = SECTIONS_CSV.exists()
    with open(SECTIONS_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SECTION_FIELDS)
        if not sections_exist:
            writer.writeheader()
        for event in all_events:
            for sec in event["sections"]:
                row = {"scraped_at": now}
                row.update(sec)
                writer.writerow(row)

    # --- Listings CSV (append) ---
    listings_exist = LISTINGS_CSV.exists()
    with open(LISTINGS_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LISTING_FIELDS)
        if not listings_exist:
            writer.writeheader()
        for event in all_events:
            for listing in event["listings"]:
                row = {"scraped_at": now}
                row.update(listing)
                writer.writerow(row)

    # --- Latest snapshot JSON ---
    snapshot = {
        "scraped_at": now,
        "event_count": len(all_events),
        "events": all_events,
    }
    LATEST_JSON.write_text(json.dumps(snapshot, indent=2, default=str))

    total_sections = sum(len(e["sections"]) for e in all_events)
    total_listings = sum(len(e["listings"]) for e in all_events)
    print(f"\nSaved at {now}:")
    print(f"  {len(all_events)} events, {total_sections} section prices, {total_listings} listings")
    print(f"  Section CSV: {SECTIONS_CSV}")
    print(f"  Listings CSV: {LISTINGS_CSV}")
    print(f"  Snapshot: {LATEST_JSON}")


def main():
    DATA_DIR.mkdir(exist_ok=True)
    p, browser, context, stealth = create_browser()

    try:
        # Create one page and reuse it — WAF tokens persist across navigations
        page = context.new_page()
        stealth.apply_stealth_sync(page)

        # Step 1: Get event URLs + viewer count from performer page
        event_links, viewer_count = get_event_urls(page)
        if not event_links:
            print("ERROR: No events found on performer page")
            return

        # Save viewer count immediately
        save_viewers(viewer_count)

        # Step 2: Scrape each event page (reusing the same page)
        all_events = []
        for i, event_link in enumerate(event_links):
            print(f"\nEvent {i + 1}/{len(event_links)}")
            result = scrape_event(page, event_link["url"])
            if result:
                all_events.append(result)
            # Polite delay between requests
            if i < len(event_links) - 1:
                time.sleep(3)

        page.close()

        # Step 3: Save data
        if all_events:
            save_data(all_events)
        else:
            print("WARNING: No event data scraped")

    finally:
        browser.close()
        p.stop()


if __name__ == "__main__":
    main()
