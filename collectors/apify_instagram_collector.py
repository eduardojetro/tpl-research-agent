"""
Instagram via Apify (apify/instagram-hashtag-scraper, keyword-search mode).
Confirmed working 2026-09-25: 8 real, on-topic posts for "postpartum
recovery" at ~$0.021 total (~$0.0026/result). See TPL_Evolution_Log.md
section 6.

IMPORTANT signal difference vs YouTube/Reddit/TikTok: Instagram's
`latestComments`/`firstComment` are mostly CTA-bait replies ("comment TEAR
and I'll DM you..."), not organic first-person problem statements -- not
usable as raw_item content. The real signal here is the POST CAPTION
itself: creators in this niche describe their audience's pain points
directly (e.g. "the biggest thing missing in postpartum care..."), and
high likes/comments validate that the pain point resonates. That means
core_problem extraction from these items will often read as "problem
described ABOUT mothers" rather than "problem described BY a mother" --
worth keeping in mind if/when tuning the v3 prompt specifically for this
source; not changed here, pipeline.py's extraction runs the same prompt
on all sources today.

Weekly cadence only (see run_weekly.py) -- not part of run_daily.py.
"""
from typing import Dict, List

from . import apify_collector

ACTOR_ID = "apify/instagram-hashtag-scraper"
PRICE_PER_1000 = 2.6  # $0.0026/result (FREE tier)


def _map_item(raw: Dict) -> Dict:
    username = raw.get("ownerUsername", "unknown")
    return {
        "url": raw.get("url", ""),
        "title": f"Instagram post by @{username}",
        "content": raw.get("caption", "") or "",
        "author": username,
        "engagement_score": raw.get("likesCount", 0) or 0,
        "comment_count": raw.get("commentsCount", 0) or 0,
        "source_name": f"instagram:@{username}",
    }


def collect(query: str, max_items: int = 15) -> List[Dict]:
    """One call, global keyword search (not per-community like Reddit) --
    Instagram doesn't have subreddit-style communities to loop over."""
    try:
        return apify_collector.collect_via_actor(
            actor_id=ACTOR_ID,
            run_input={
                "hashtags": [query],
                "keywordSearch": True,
                "resultsType": "posts",
                "resultsLimit": max_items,
            },
            item_mapper=_map_item,
            max_items=max_items,
            price_per_1000=PRICE_PER_1000,
            source_name=f"instagram:{query}",
        )
    except Exception as exc:
        print(f"[apify_instagram] SKIPPED: {exc}")
        return []
