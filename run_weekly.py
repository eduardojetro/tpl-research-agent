"""
Weekly unattended run: Instagram + TikTok collection only, for every theme
in sources_seed.THEMES.

Deliberately collection-only, no extraction here -- run_daily.py's
extract_theme() already processes ANY unprocessed raw_items regardless of
which collector put them there, so duplicating that logic (and paying for
a second LLM pass) here would be redundant. This script's whole job is to
top up raw_items; the next daily run picks them up naturally.

Why weekly, not daily: Instagram + TikTok are real money (unlike YouTube's
free official API), and running Reddit (also Apify now) + Instagram +
TikTok all daily was estimated at ~$1/day -- more than this project's
budget justifies for content-validation-stage volume. Reddit stays daily
(cheapest of the three, and the only one with no free official
alternative pending), Instagram/TikTok move to weekly. Revisit the split
if paid content validates strongly enough to justify more spend.

Facebook is NOT included -- the Apify group-search actor tested
2026-09-25 returned 0 results for two different real queries (broken or
blocked, not a config issue on our end). Needs either a different actor
or manually-curated real group URLs (like sources_seed.py's subreddits
list) before it's worth wiring in.

Meant to be run once/week via Windows Task Scheduler through
run_weekly.bat (mirrors run_daily.bat's Doppler-token pattern). Logs to
logs/weekly_<date>.log in addition to stdout.
"""
import sys
import os
from datetime import datetime

import pipeline
import sources_seed
from collectors import apify_instagram_collector, apify_tiktok_collector
from run_daily import Tee  # reuse the same dual stdout+file logger

MAX_ITEMS_PER_THEME = 15


def collect_theme_weekly(seed_theme: str):
    theme = sources_seed.get_theme(seed_theme)
    query = theme["search_query"]

    ig_items = apify_instagram_collector.collect(query, max_items=MAX_ITEMS_PER_THEME)
    n_ig = pipeline._store_items(ig_items, seed_theme, platform="instagram")
    print(f"[weekly] {seed_theme} instagram: {n_ig} new raw_items inserted ({len(ig_items)} fetched, rest were dupes).")

    tiktok_items = apify_tiktok_collector.collect(query, max_items=MAX_ITEMS_PER_THEME)
    n_tt = pipeline._store_items(tiktok_items, seed_theme, platform="tiktok")
    print(f"[weekly] {seed_theme} tiktok: {n_tt} new raw_items inserted ({len(tiktok_items)} fetched, rest were dupes).")

    return n_ig + n_tt


def main():
    print(f"\n{'=' * 60}\nWeekly Instagram+TikTok collection run: {datetime.now().isoformat()}\n{'=' * 60}")
    total = 0
    for theme in sources_seed.THEMES:
        try:
            total += collect_theme_weekly(theme)
        except Exception as exc:
            print(f"[weekly] {theme} FAILED, moving to next theme: {exc}")
    print(f"\nWeekly run finished: {datetime.now().isoformat()}. Total new raw_items: {total}")
    print("(No extraction here -- tomorrow's daily run picks these up automatically.)")


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/weekly_{datetime.now().strftime('%Y-%m-%d')}.log"
    log_file = open(log_path, "a", encoding="utf-8")
    sys.stdout = Tee(sys.stdout, log_file)
    sys.stderr = Tee(sys.stderr, log_file)
    main()
