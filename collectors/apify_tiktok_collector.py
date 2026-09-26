"""
TikTok via Apify (xmolodtsov/tiktok-search-scraper, keyword search).
Confirmed working 2026-09-25: 10 real items for "postpartum recovery" at
~$0.003 total (~$0.0003/video -- the cheapest of all sources tested). See
TPL_Evolution_Log.md section 6.

Unlike Instagram, TikTok video captions here are genuine first-person mom
content (e.g. "I haven't worn normal clothes since giving birth... day 3
postpartum"), the same organic voice as YouTube comments and Reddit posts
-- no source-specific prompt caveat needed, extraction works the same as
everywhere else.

Note: this actor's free plan caps each run at ~10 results and ~5 runs/month
regardless of the `maxItems` input (a developer-set limit, not Apify's) --
fine for testing, but means it can't be scaled up on the free plan alone;
revisit if daily/weekly volume needs exceed that.

Weekly cadence only (see run_weekly.py) -- not part of run_daily.py.
"""
from typing import Dict, List

from . import apify_collector

ACTOR_ID = "xmolodtsov/tiktok-search-scraper"
PRICE_PER_1000 = 0.3  # $0.0003/video (FREE tier)


def _map_item(raw: Dict) -> Dict:
    channel = raw.get("channel", {}) or {}
    username = channel.get("username", "unknown")
    return {
        "url": raw.get("postPage", ""),
        "title": f"TikTok video by @{username}",
        "content": raw.get("title", "") or "",  # this actor's "title" field is actually the video caption
        "author": username,
        "engagement_score": raw.get("likes", 0) or 0,
        "comment_count": raw.get("comments", 0) or 0,
        "source_name": f"tiktok:@{username}",
    }


def collect(query: str, max_items: int = 15) -> List[Dict]:
    """One call, global keyword search across all of TikTok."""
    try:
        return apify_collector.collect_via_actor(
            actor_id=ACTOR_ID,
            run_input={
                "keywords": [query],
                "maxItems": max_items,
            },
            item_mapper=_map_item,
            max_items=max_items,
            price_per_1000=PRICE_PER_1000,
            source_name=f"tiktok:{query}",
        )
    except Exception as exc:
        print(f"[apify_tiktok] SKIPPED: {exc}")
        return []
