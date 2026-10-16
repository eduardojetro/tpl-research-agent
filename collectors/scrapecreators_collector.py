"""
OPTIONAL TikTok / Instagram keyword search via ScrapeCreators. OFF by default.

Endpoints and response shapes adapted from last30days (MIT, mvanhorn)
scripts/lib/tiktok.py and instagram.py.

Not wired into run_daily / run_weekly / drain_backlog / any workflow, and not
imported anywhere. Nothing happens unless a human calls collect() explicitly
AND SCRAPECREATORS_API_KEY is set; without the key it raises
ScrapeCreatorsNotConfigured before any network access.

COST CAVEAT: ScrapeCreators is a paid API. As of last30days' docs the account
comes with ~100 free calls and then pay-as-you-go. Every collect() call is ONE
billable request (no retries; the skill's Instagram 500-retry fallback was left out).
Eduardo decides whether to open an account; this module was written and tested
without ever contacting the service. Treat the response field names as
unverified against the live API until the first real call is made.

Return shape matches apify_tiktok_collector / apify_instagram_collector, so
pipeline._store_items(items, seed_theme, platform=...) accepts it unchanged.
Like the Apify collectors, content is the post caption, not comments.

Usage (manual, one billable call):
    SCRAPECREATORS_API_KEY=... python -c "from collectors import scrapecreators_collector as s; print(s.collect('postpartum recovery', 'tiktok', 10))"
"""
import os
from typing import Dict, List

import requests

BASE = "https://api.scrapecreators.com"
ENV_KEY = "SCRAPECREATORS_API_KEY"
TIMEOUT_SECONDS = 30


class ScrapeCreatorsNotConfigured(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise ScrapeCreatorsNotConfigured(
            f"{ENV_KEY} is not set -- the ScrapeCreators collector is optional and "
            f"disabled until Eduardo opens an account and adds the key."
        )
    return key


def _get(path: str, params: Dict, key: str) -> Dict:
    response = requests.get(
        f"{BASE}{path}", params=params,
        headers={"x-api-key": key, "Content-Type": "application/json"},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def _map_tiktok(raw: Dict) -> Dict:
    stats = raw.get("statistics") if isinstance(raw.get("statistics"), dict) else {}
    author = raw.get("author")
    username = (author.get("unique_id") if isinstance(author, dict) else author) or "unknown"
    video_id = str(raw.get("aweme_id", ""))
    url = (raw.get("share_url") or "").split("?")[0]
    if not url and video_id and username != "unknown":
        url = f"https://www.tiktok.com/@{username}/video/{video_id}"
    return {
        "url": url,
        "title": f"TikTok video by @{username}",
        "content": raw.get("desc", "") or "",
        "author": username,
        "engagement_score": stats.get("digg_count") or 0,
        "comment_count": stats.get("comment_count") or 0,
        "source_name": f"tiktok:@{username}",
    }


def _map_instagram(raw: Dict) -> Dict:
    caption = raw.get("caption", "")
    text = caption.get("text", "") if isinstance(caption, dict) else (caption or "")
    owner = raw.get("owner") or raw.get("user")
    username = (owner.get("username") if isinstance(owner, dict) else owner) or "unknown"
    url = raw.get("url", "")
    shortcode = raw.get("shortcode") or raw.get("code")
    if not url and shortcode:
        url = f"https://www.instagram.com/reel/{shortcode}"
    return {
        "url": url,
        "title": f"Instagram reel by @{username}",
        "content": text or "",
        "author": username,
        "engagement_score": raw.get("like_count") or 0,
        "comment_count": raw.get("comment_count") or 0,
        "source_name": f"instagram:@{username}",
    }


def collect(query: str, platform: str, max_items: int = 15) -> List[Dict]:
    """One billable keyword search. platform is "tiktok" or "instagram".

    Raises ScrapeCreatorsNotConfigured if the key is missing (before any
    request). Network/HTTP errors propagate to the caller; nothing is retried,
    so a failure never silently spends more credits.
    """
    if platform not in ("tiktok", "instagram"):
        raise ValueError(f"platform must be 'tiktok' or 'instagram', got {platform!r}")
    key = _api_key()

    if platform == "tiktok":
        data = _get("/v1/tiktok/search/keyword", {"query": query, "sort_by": "relevance"}, key)
        entries = data.get("search_item_list") or data.get("data") or []
        raws = [e.get("aweme_info", e) for e in entries if isinstance(e, dict)]
        mapper = _map_tiktok
    else:
        data = _get("/v2/instagram/reels/search", {"query": query}, key)
        raws = [r for r in (data.get("reels") or data.get("items") or data.get("data") or []) if isinstance(r, dict)]
        mapper = _map_instagram

    items = [mapper(r) for r in raws[:max_items]]
    return [i for i in items if i["url"] and i["content"]]
