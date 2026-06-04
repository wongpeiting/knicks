# Data Dictionary

## section_prices.csv — Section-level pricing (~510 rows/scrape)

| Column | Type | Example | Description |
|---|---|---|---|
| scraped_at | datetime | 2026-06-04T22:00:35Z | UTC timestamp of scrape |
| event_id | int | 160287605 | StubHub event ID (stable, joinable) |
| event_name | string | New York Knicks at San Antonio Spurs: NBA Finals (Home Game 2, Series Game 2) | Full event title |
| event_date | datetime | 2026-06-05T19:30:00 | Event start time (local) |
| venue | string | Frost Bank Center (Formerly AT&T Center) | Venue name |
| section_key | string | 329_149071 | Composite key: {ticket_class_id}_{section_id}. Stable across scrapes — use this to track a section over time |
| section_name | string | 225 | Human-readable section number. Can be blank if not in venue config |
| ticket_class | string | 329 | Ticket class ID (maps to ticket_class_name) |
| ticket_class_name | string | Balcony | Tier/level name (e.g. 100 Level, Club Gold, Delta Sky360 Club) |
| min_price | float | 797.79 | Cheapest listing price in this section (USD, includes fees) |
| listing_count | int | 5 | Number of active seller listings in this section |
| ticket_count | int | 12 | Total individual seats for sale across all listings in this section |
| row | string | 12 | Row of the cheapest listing in this section |

## listings.csv — Individual ticket listings (240 rows/scrape)

| Column | Type | Example | Description |
|---|---|---|---|
| scraped_at | datetime | 2026-06-04T22:00:35Z | UTC timestamp of scrape |
| event_id | int | 160287605 | StubHub event ID |
| event_name | string | (same as above) | Full event title |
| event_date | datetime | 2026-06-05T19:30:00 | Event start time |
| venue | string | Frost Bank Center | Venue name |
| listing_id | int | 12933171940 | Unique listing ID — stable as long as the seller's listing is active. Join across scrapes to track individual price changes |
| section | string | 225 | Section number |
| section_id | int | 149091 | StubHub internal section ID |
| ticket_class_name | string | Balcony | Tier/level name |
| row | string | 16 | Row number |
| seat_from | string | 3 | First seat number (can be blank if not disclosed) |
| seat_to | string | 4 | Last seat number (can be blank) |
| raw_price | float | 1027.07 | Price per ticket (USD, includes fees) |
| formatted_price | string | $1,027 | Display price |
| available_tickets | int | 2 | Number of tickets in this listing |
| ticket_type | string | Mobile Transfer ticket | Delivery method |

Note: Only captures the top 40 listings per event (StubHub's first page, sorted by "Recommended"). Not exhaustive — use section_prices.csv for full coverage of all sections.

## viewers.csv — Page traffic (1 row/scrape)

| Column | Type | Example | Description |
|---|---|---|---|
| scraped_at | datetime | 2026-06-04T22:20:54Z | UTC timestamp |
| viewers_past_hour | int | 10422 | Number of people who viewed Knicks events on StubHub in the past hour (as reported by StubHub) |
