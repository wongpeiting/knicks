"""
StubHub Knicks Ticket Price Scraper

Architecture follows Bill's PA Senate scraper pattern:
  1. Load previous scrape for change detection
  2. Scrape current data
  3. Diff against previous: additions, deletions, modifications
  4. Write data + change log + error log

Output:
  - data/section_prices.csv     (append)
  - data/listings.csv           (append)
  - data/event_summary.csv      (append)
  - data/viewers.csv            (append)
  - data/last_scrape.json       (overwritten — baseline for next diff)
  - data/changelogs/{ts}.json   (per-scrape, only if changes)
  - data/error_logs/{ts}.json   (per-scrape, only if errors)

Requires: playwright, playwright-stealth
"""

import csv
import json
import random
import re
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

PERFORMER_URL = "https://www.stubhub.com/new-york-knicks-tickets/performer/2742"
DATA_DIR = Path(__file__).parent / "data"
SECTIONS_CSV = DATA_DIR / "section_prices.csv"
LISTINGS_CSV = DATA_DIR / "listings.csv"
ROW_PRICES_CSV = DATA_DIR / "row_prices.csv"
VIEWERS_CSV = DATA_DIR / "viewers.csv"
EVENT_SUMMARY_CSV = DATA_DIR / "event_summary.csv"
LAST_SCRAPE_JSON = DATA_DIR / "last_scrape.json"

EVENT_SUMMARY_FIELDS = [
    "scraped_at", "event_id", "event_name", "event_date", "venue",
    "total_listings", "min_price", "max_price", "tag",
]

SECTION_FIELDS = [
    "scraped_at", "event_id", "event_name", "event_date", "venue",
    "section_key", "section_name", "ticket_class", "ticket_class_name",
    "min_price", "listing_count", "ticket_count", "row",
]

ROW_PRICE_FIELDS = [
    "scraped_at", "event_id", "event_name", "event_date", "venue",
    "row_key", "min_price", "listing_count", "ticket_count",
    "listing_id",
]

LISTING_FIELDS = [
    "scraped_at", "event_id", "event_name", "event_date", "venue",
    "listing_id", "section", "section_id", "ticket_class_name",
    "row", "seat_from", "seat_to", "raw_price", "formatted_price",
    "available_tickets", "ticket_type",
]

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]


# ── Step 1: Load previous scrape ──────────────────────────────────────


def load_previous_scrape():
    """Load last_scrape.json and build lookup maps for diffing."""
    if LAST_SCRAPE_JSON.exists():
        with open(LAST_SCRAPE_JSON) as f:
            prev = json.load(f)
        old_events = {e["event_id"]: e for e in prev.get("events", [])}
        old_listings = {}
        for e in prev.get("events", []):
            for li in e.get("listings", []):
                old_listings[li["listing_id"]] = li
        print(f"Loaded previous scrape: {prev['scraped_at']}")
        print(f"  {len(old_events)} events, {len(old_listings)} listings")
        return old_events, old_listings
    print("No previous scrape found. First run.")
    return {}, {}


# ── Step 2: Browser + scraping functions ──────────────────────────────


def create_browser():
    """Create a stealth Playwright browser context with randomized fingerprint."""
    p = sync_playwright().start()
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport={"width": 1920, "height": 1080},
        locale="en-US",
    )
    stealth = Stealth()
    return p, browser, context, stealth


def wait_for_full_page(page, timeout_s=45):
    """Wait for WAF challenge to resolve and real page to load."""
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


def extract_event_data(html):
    """Parse embedded JSON from event page HTML."""
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
        if isinstance(data, dict) and data.get("appName") == "viagogo-event" and "grid" in data:
            event_data = data
        if isinstance(data, dict) and data.get("@type") in ("Event", "SportsEvent"):
            event_meta = data
    return event_data, event_meta


def get_event_urls(page):
    """Load performer page, extract event URLs + metadata."""
    print(f"Loading performer page: {PERFORMER_URL}")
    page.goto(PERFORMER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(15)

    title = page.title()
    print(f"  Title: {title}")

    body_text = page.inner_text("body")

    viewer_count = 0
    m = re.search(r"([\d,]+)\s+people viewed", body_text)
    if m:
        viewer_count = int(m.group(1).replace(",", ""))

    followers = ""
    m = re.search(r"([\d.]+[KMkm]?)\n", body_text)
    if m:
        followers = m.group(1)

    event_count = 0
    m = re.search(r"(\d+)\s+(?:playoff\s+)?events?", body_text)
    if m:
        event_count = int(m.group(1))

    page_meta = {
        "viewers_past_hour": viewer_count,
        "followers": followers,
        "event_count": event_count,
    }
    print(f"  Viewers: {viewer_count:,} | Followers: {followers} | Events: {event_count}")

    raw_links = page.eval_on_selector_all(
        'a[href*="/event/"]',
        """els => els.map(e => ({
            href: e.href,
            text: e.innerText.replace(/\\n/g, ' | ').substring(0, 400)
        }))"""
    )

    TAG_KEYWORDS = [
        "Hottest event", "Selling fast", "Best value",
        "Popular", "Almost sold out", "Limited availability",
    ]

    seen = set()
    events = []
    for link in raw_links:
        url = link["href"].split("?")[0]
        if url in seen:
            continue
        seen.add(url)
        text = link["text"].strip()
        if text and ("knick" in text.lower() or "nyk" in text.lower()
                      or "spurs" in text.lower() or "nba" in text.lower()
                      or "game" in text.lower()):
            tag = ""
            for kw in TAG_KEYWORDS:
                if kw.lower() in text.lower():
                    tag = kw
                    break
            events.append({"url": url, "text": text, "tag": tag})

    print(f"  Found {len(events)} Knicks events")
    return events, page_meta


def scrape_event(page, event_url, max_retries=2):
    """Scrape a single event page. Returns result dict or None."""
    for attempt in range(max_retries + 1):
        captured_html = {}

        def on_response(response):
            if ("/event/" in response.url
                    and "text/html" in response.headers.get("content-type", "")
                    and response.status == 200):
                try:
                    body = response.text()
                    if len(body) > 100000:
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
        return None

    event_data, event_meta = extract_event_data(html)
    if not event_data:
        return None

    grid = event_data["grid"]
    event_id = grid.get("eventId", "")
    event_name, event_date, venue = "", "", ""
    if event_meta:
        event_name = event_meta.get("name", "")
        event_date = event_meta.get("startDate", "")
        loc = event_meta.get("location", {})
        if isinstance(loc, dict):
            venue = loc.get("name", "")

    tc_names = grid.get("availableTicketClassPairs", {})
    section_popup = grid.get("venueMapData", {}).get("sectionPopupData", {})
    venue_config = event_data.get("venueConfiguration", {})

    sections = []
    for key, sec in section_popup.items():
        parts = key.split("_", 1)
        tc_id = parts[0] if len(parts) > 1 else ""
        section_name = venue_config.get(key, {}).get("sectionName", "")
        sections.append({
            "event_id": event_id, "event_name": event_name,
            "event_date": event_date, "venue": venue,
            "section_key": key, "section_name": section_name,
            "ticket_class": tc_id,
            "ticket_class_name": tc_names.get(tc_id, ""),
            "min_price": sec.get("rawMinPrice", ""),
            "listing_count": sec.get("count", 0),
            "ticket_count": sec.get("ticketCount", 0),
            "row": sec.get("rowText", ""),
        })

    # --- Row-level data (covers nearly ALL listings) ---
    row_popup = grid.get("venueMapData", {}).get("rowPopupData", {})
    row_prices = []
    for key, rp in row_popup.items():
        row_prices.append({
            "event_id": event_id, "event_name": event_name,
            "event_date": event_date, "venue": venue,
            "row_key": key,
            "min_price": rp.get("rawMinPrice", ""),
            "listing_count": rp.get("count", 0),
            "ticket_count": rp.get("ticketCount", 0),
            "listing_id": rp.get("listingId", ""),
        })

    # --- Individual listings (top 40 recommended) ---
    listings = []
    for item in grid.get("items", []):
        listings.append({
            "event_id": event_id, "event_name": event_name,
            "event_date": event_date, "venue": venue,
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
        "event_id": event_id, "event_name": event_name,
        "event_date": event_date, "venue": venue,
        "event_url": event_url,
        "min_price": grid.get("minPrice", ""),
        "max_price": grid.get("maxPrice", ""),
        "total_listings": grid.get("totalCount", grid.get("totalFilteredListings", 0)),
        "ticket_classes": tc_names,
        "sections": sections,
        "row_prices": row_prices,
        "listings": listings,
    }

    print(f"    {event_name}")
    print(f"    {len(sections)} sections, {len(row_prices)} rows, {len(listings)} listings, "
          f"${grid.get('minPrice', '?'):,.0f} - ${grid.get('maxPrice', '?'):,.0f}")
    return result


# ── Step 3: Change detection ─────────────────────────────────────────


def build_changelog(all_events, old_events, old_listings):
    """Compare current scrape against previous. Returns changelog dict."""
    changelog = {
        "new_events": [],
        "removed_events": [],
        "modifications": [],
        "new_listings": [],
        "removed_listings": [],
        "price_changes": [],
    }

    current_event_ids = {e["event_id"] for e in all_events}

    # New and modified events
    for event in all_events:
        eid = event["event_id"]
        if eid not in old_events:
            changelog["new_events"].append({
                "event_id": eid,
                "event_name": event["event_name"],
                "min_price": event["min_price"],
                "total_listings": event["total_listings"],
            })
        else:
            old_e = old_events[eid]
            changes = {}
            for field in ["min_price", "max_price", "total_listings", "tag"]:
                if old_e.get(field) != event.get(field):
                    changes[field] = {"from": old_e.get(field), "to": event.get(field)}
            if changes:
                changelog["modifications"].append({
                    "event_id": eid,
                    "event_name": event["event_name"],
                    "changes": changes,
                })

    # Deleted events
    for eid, old_e in old_events.items():
        if eid not in current_event_ids:
            changelog["removed_events"].append({
                "event_id": eid,
                "event_name": old_e.get("event_name", ""),
            })

    # Listing-level diffs
    current_listings = {}
    for e in all_events:
        for li in e.get("listings", []):
            current_listings[li["listing_id"]] = li

    for lid, li in current_listings.items():
        if lid not in old_listings:
            changelog["new_listings"].append({
                "listing_id": lid, "event_id": li["event_id"],
                "section": li["section"], "row": li["row"],
                "price": li["raw_price"],
            })
        else:
            old_price = old_listings[lid].get("raw_price")
            new_price = li.get("raw_price")
            if old_price != new_price:
                changelog["price_changes"].append({
                    "listing_id": lid, "event_id": li["event_id"],
                    "section": li["section"], "row": li["row"],
                    "from": old_price, "to": new_price,
                })

    for lid, li in old_listings.items():
        if lid not in current_listings:
            changelog["removed_listings"].append({
                "listing_id": lid, "event_id": li["event_id"],
                "section": li["section"], "row": li["row"],
                "last_price": li["raw_price"],
            })

    return changelog


# ── Step 4: Save everything ──────────────────────────────────────────


def save_data(all_events, page_meta, changelog, error_log, now_str):
    """Write CSVs, snapshot, change log, and error log."""
    now_file = now_str.replace(":", "-").replace("Z", "")

    # ── Viewers CSV ──
    v_exists = VIEWERS_CSV.exists()
    with open(VIEWERS_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["scraped_at", "viewers_past_hour", "followers", "event_count"])
        if not v_exists:
            w.writeheader()
        w.writerow({"scraped_at": now_str, **page_meta})

    # ── Section prices CSV ──
    sp_exists = SECTIONS_CSV.exists()
    with open(SECTIONS_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SECTION_FIELDS)
        if not sp_exists:
            w.writeheader()
        for event in all_events:
            for sec in event["sections"]:
                w.writerow({"scraped_at": now_str, **sec})

    # ── Listings CSV ──
    li_exists = LISTINGS_CSV.exists()
    with open(LISTINGS_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LISTING_FIELDS)
        if not li_exists:
            w.writeheader()
        for event in all_events:
            for li in event["listings"]:
                w.writerow({"scraped_at": now_str, **li})

    # ── Row prices CSV (all section+row combos) ──
    rp_exists = ROW_PRICES_CSV.exists()
    with open(ROW_PRICES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ROW_PRICE_FIELDS)
        if not rp_exists:
            w.writeheader()
        for event in all_events:
            for rp in event.get("row_prices", []):
                w.writerow({"scraped_at": now_str, **rp})

    # ── Event summary CSV ──
    es_exists = EVENT_SUMMARY_CSV.exists()
    with open(EVENT_SUMMARY_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=EVENT_SUMMARY_FIELDS)
        if not es_exists:
            w.writeheader()
        for event in all_events:
            w.writerow({
                "scraped_at": now_str,
                "event_id": event["event_id"],
                "event_name": event["event_name"],
                "event_date": event["event_date"],
                "venue": event["venue"],
                "total_listings": event["total_listings"],
                "min_price": event["min_price"],
                "max_price": event["max_price"],
                "tag": event.get("tag", ""),
            })

    # ── Save snapshot for next run's diff ──
    snapshot = {
        "scraped_at": now_str,
        "event_count": len(all_events),
        "events": all_events,
    }
    LAST_SCRAPE_JSON.write_text(json.dumps(snapshot, indent=2, default=str))

    # ── Change log (only if changes exist) ──
    has_changes = any(changelog[k] for k in changelog)
    if has_changes:
        cl_dir = DATA_DIR / "changelogs"
        cl_dir.mkdir(exist_ok=True)
        cl_path = cl_dir / f"{now_file}.json"
        cl_path.write_text(json.dumps(
            {"date": now_str, **changelog}, indent=2, default=str
        ))
        print(f"  Change log: {cl_path}")

    # ── Error log (only if errors exist) ──
    if error_log:
        el_dir = DATA_DIR / "error_logs"
        el_dir.mkdir(exist_ok=True)
        el_path = el_dir / f"{now_file}.json"
        el_path.write_text(json.dumps(
            {"date": now_str, "errors": error_log}, indent=2, default=str
        ))
        print(f"  Error log: {el_path}")

    total_sections = sum(len(e["sections"]) for e in all_events)
    total_listings = sum(len(e["listings"]) for e in all_events)
    print(f"\nSaved at {now_str}:")
    total_rows = sum(len(e.get("row_prices", [])) for e in all_events)
    print(f"  {len(all_events)} events, {total_sections} sections, {total_rows} rows, {total_listings} listings")
    if has_changes:
        print(f"  Changes: +{len(changelog['new_events'])} events, "
              f"-{len(changelog['removed_events'])} events, "
              f"{len(changelog['modifications'])} modified, "
              f"+{len(changelog['new_listings'])} listings, "
              f"-{len(changelog['removed_listings'])} listings, "
              f"{len(changelog['price_changes'])} repriced")
    if error_log:
        print(f"  Errors: {len(error_log)}")


# ── Main ─────────────────────────────────────────────────────────────


def main():
    DATA_DIR.mkdir(exist_ok=True)

    # Random delay to avoid looking like a bot swarm
    delay = random.randint(10, 90)
    print(f"Startup delay: {delay}s")
    time.sleep(delay)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── Step 1: Load previous scrape for diffing ──
    old_events, old_listings = load_previous_scrape()

    # ── Step 2: Scrape ──
    error_log = []
    p, browser, context, stealth = create_browser()

    try:
        page = context.new_page()
        stealth.apply_stealth_sync(page)

        event_links, page_meta = get_event_urls(page)
        if not event_links:
            print("ERROR: No events found on performer page")
            return

        all_events = []
        for i, event_link in enumerate(event_links):
            print(f"\nEvent {i + 1}/{len(event_links)}")
            try:
                result = scrape_event(page, event_link["url"])
                if result:
                    result["tag"] = event_link.get("tag", "")
                    all_events.append(result)
                else:
                    # Fallback: keep previous scrape's data for this event
                    for old_e in old_events.values():
                        if old_e.get("event_url") == event_link["url"]:
                            print(f"    Fallback: using previous scrape data")
                            all_events.append(old_e)
                            break
                    error_log.append({
                        "url": event_link["url"],
                        "error_type": "EmptyResponse",
                        "message": "No listing data captured from page",
                    })
            except Exception as e:
                print(f"    ERROR: {e}")
                # Fallback: keep previous data
                for old_e in old_events.values():
                    if old_e.get("event_url") == event_link["url"]:
                        print(f"    Fallback: using previous scrape data")
                        all_events.append(old_e)
                        break
                error_log.append({
                    "url": event_link["url"],
                    "error_type": type(e).__name__,
                    "message": str(e),
                    "traceback": traceback.format_exc().splitlines()[-3:],
                })

            if i < len(event_links) - 1:
                time.sleep(3)

        page.close()
    finally:
        browser.close()
        p.stop()

    if not all_events:
        print("WARNING: No event data scraped")
        return

    # ── Step 3: Diff against previous scrape ──
    changelog = build_changelog(all_events, old_events, old_listings)

    # ── Step 4: Save everything ──
    save_data(all_events, page_meta, changelog, error_log, now_str)


if __name__ == "__main__":
    main()
