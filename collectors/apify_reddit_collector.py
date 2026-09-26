"""
Reddit via Apify (trudax/reddit-scraper-lite), used while the official PRAW
app (collectors/reddit_collector.py) waits on Reddit's approval queue.
Confirmed working with a real 15-item test run against r/BabyBumps on
2026-09-25 (see TPL_Evolution_Log.md section 5) -- real, on-topic results
(e.g. "Epidural with scoliosis?", a postpartum hemorrhage birth story).

Cost: PAY_PER_EVENT, $0.02 flat per run (per GB memory) + $0.004/result.
A 20-item run costs roughly $0.10 -- trivial against the $5/month free
credit, but real money, unlike the YouTube official API. Every call here
still goes through apify_collector.collect_via_actor's hard max_items
ceiling and cost estimate print, same safety net as the rest of this file.

Known gap (not solved here): unlike youtube_collector's exclude_video_ids,
this actor has no "skip these IDs" input, so there's no way to avoid PAYING
for a result that turns out to already be in raw_items -- the URL-uniqueness
dedupe (db.insert_raw_item) still stops it from being stored twice, but not
from being billed once. Mitigated by sort="new" (biases toward content we
haven't seen yet), not eliminated. Fine at today's tiny per-theme volumes;
revisit if daily volume grows enough for this to matter.
"""
from typing import Dict, List

from . import apify_collector

ACTOR_ID = "trudax/reddit-scraper-lite"
PRICE_PER_1000 = 4.0  # $0.004/result


def _map_item(raw: Dict) -> Dict:
    is_post = raw.get("dataType") == "post"
    return {
        "url": raw.get("url", ""),
        "title": raw.get("title") or f"Reddit comment in r/{raw.get('parsedCommunityName', '')}",
        "content": raw.get("body", ""),
        "author": raw.get("username", "unknown"),
        # Not available from this actor without includeMediaLinks=True
        # (which switches to a slower extraction method) -- left at 0
        # rather than guessing. Doesn't block extraction: pipeline.py's
        # MIN_CONTENT_CHARS filter and the LLM step don't depend on it.
        "engagement_score": 0,
        "comment_count": 0,
        "source_name": f"reddit_apify:r/{raw.get('parsedCommunityName', '')}",
        "is_post": is_post,
    }


def collect(query: str, subreddits: List[str], max_items_per_subreddit: int = 15) -> List[Dict]:
    """
    Runs one Apify actor call per subreddit (this actor's searchCommunityName
    takes a single community, not a list) -- cost scales with
    len(subreddits) x max_items_per_subreddit, so keep max_items_per_subreddit
    modest. sort="new" biases each run toward content not seen in a previous
    run, since there's no ID-exclusion input to rely on instead.
    """
    items = []
    for subreddit in subreddits:
        try:
            raw_items = apify_collector.collect_via_actor(
                actor_id=ACTOR_ID,
                run_input={
                    "searches": [query],
                    "searchCommunityName": subreddit,
                    "searchPosts": True,
                    "searchComments": True,
                    "sort": "new",
                    "maxItems": max_items_per_subreddit,
                    "maxComments": 5,
                    "includeMediaLinks": False,
                },
                item_mapper=_map_item,
                max_items=max_items_per_subreddit,
                price_per_1000=PRICE_PER_1000,
                source_name=f"reddit_apify:r/{subreddit}",
            )
            items.extend(raw_items)
        except Exception as exc:
            print(f"[apify_reddit] r/{subreddit} SKIPPED: {exc}")
    return items
