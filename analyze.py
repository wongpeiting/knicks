"""
Analyze scraped StubHub Knicks ticket price data.
Shows price trends per section over time.
"""

import pandas as pd
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
SECTIONS_CSV = DATA_DIR / "section_prices.csv"
LISTINGS_CSV = DATA_DIR / "listings.csv"


def analyze_sections():
    if not SECTIONS_CSV.exists():
        print("No section data yet. Run scraper.py first.")
        return

    df = pd.read_csv(SECTIONS_CSV, parse_dates=["scraped_at"])
    df["min_price"] = pd.to_numeric(df["min_price"], errors="coerce")

    print(f"=== SECTION PRICE DATA ===")
    print(f"Records: {len(df)}")
    print(f"Scrapes: {df['scraped_at'].nunique()}")
    print(f"Events: {df['event_name'].nunique()}")
    print(f"Date range: {df['scraped_at'].min()} to {df['scraped_at'].max()}")
    print()

    # Per event summary
    for event_name, group in df.groupby("event_name"):
        print(f"\n--- {event_name} ---")
        print(f"  Sections: {group['section_key'].nunique()}")
        print(f"  Price range: ${group['min_price'].min():,.0f} - ${group['min_price'].max():,.0f}")

        # If multiple scrapes, show price changes
        if group["scraped_at"].nunique() > 1:
            pivot = group.pivot_table(
                index="section_key",
                columns="scraped_at",
                values="min_price",
            )
            first_col = pivot.columns[0]
            last_col = pivot.columns[-1]
            pivot["change"] = pivot[last_col] - pivot[first_col]
            pivot["change_pct"] = (pivot["change"] / pivot[first_col] * 100).round(1)

            movers = pivot.dropna(subset=["change"]).sort_values("change")
            if len(movers) > 0:
                print(f"\n  Biggest drops:")
                for idx, row in movers.head(5).iterrows():
                    print(f"    {idx}: ${row[first_col]:,.0f} -> ${row[last_col]:,.0f} ({row['change_pct']:+.1f}%)")
                print(f"\n  Biggest rises:")
                for idx, row in movers.tail(5).iterrows():
                    print(f"    {idx}: ${row[first_col]:,.0f} -> ${row[last_col]:,.0f} ({row['change_pct']:+.1f}%)")

    # Ticket class summary
    print("\n\n=== BY TICKET CLASS ===")
    class_summary = (
        df.groupby("ticket_class_name")
        .agg(
            sections=("section_key", "nunique"),
            avg_price=("min_price", "mean"),
            min_price=("min_price", "min"),
            max_price=("min_price", "max"),
        )
        .round(0)
        .sort_values("avg_price")
    )
    print(class_summary.to_string())


def analyze_listings():
    if not LISTINGS_CSV.exists():
        print("No listing data yet.")
        return

    df = pd.read_csv(LISTINGS_CSV, parse_dates=["scraped_at"])
    df["raw_price"] = pd.to_numeric(df["raw_price"], errors="coerce")

    print(f"\n\n=== INDIVIDUAL LISTINGS ===")
    print(f"Records: {len(df)}")
    print(f"Price range: ${df['raw_price'].min():,.0f} - ${df['raw_price'].max():,.0f}")
    print(f"Sections: {df['section'].nunique()}")
    print()

    # Show cheapest listings
    latest = df[df["scraped_at"] == df["scraped_at"].max()]
    print("Cheapest current listings:")
    for _, row in latest.nsmallest(10, "raw_price").iterrows():
        print(f"  Section {row['section']} Row {row['row']}: "
              f"${row['raw_price']:,.0f} ({row['ticket_class_name']})")


if __name__ == "__main__":
    analyze_sections()
    analyze_listings()
