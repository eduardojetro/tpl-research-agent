"""
YouTube collector -- official YouTube Data API v3, no OAuth, no anti-bot risk
(unlike Reddit, which is currently blocked pending app approval). Ported
from the working Node.js version already in production at
C:\\Projects\\Ferramentas\\dashboard\\api\\youtube-comments.js
(github.com/eduardojetro/venthora-ferramentas) -- that script pages through
ALL comments+replies for one videoId; this adds a search step on top so the
pipeline can go from a theme/topic straight to comments across many videos.

Quota cost (free tier: 10,000 units/day):
  - search.list       = 100 units per call (returns up to 50 videos)
  - videos.list        = 1 unit per call (up to 50 ids) -- used to rank by
                          actual discussion volume, not just search-text match
  - commentThreads.list = 1 unit per page (100 comments/page)
A typical theme run (1 search + 1 stats call + ~10 videos x ~1-2 comment
pages) costs roughly 130-160 units -- about 1.5% of the free daily quota.

Two-stage selection, deliberately kept separate:
  1. Cheap triage HERE (video discussion volume, order=relevance on
     comments) -- decides what's worth sending to the LLM at all, purely to
     stay in budget. It is not a judgment about which problems matter.
  2. Actual semantic judgment happens in the LLM extraction step
     (pipeline.py) -- pain/intent/gap scores. That's the real intelligence;
     this file just keeps it from drowning in low-signal comments.
"""
import csv
import os
from datetime import date

import requests

import config

API_BASE = "https://www.googleapis.com/youtube/v3"

# Cost per call in YouTube API "units" (see module docstring). The free tier
# is a hard 10,000 units/day -- going over just returns a 403/429
# quotaExceeded error, it does NOT auto-bill, so there's no accidental-spend
# risk to guard against. This counter exists purely for VISIBILITY (the
# Cloud Console dashboard shows real usage too, but with lag, and only if
# you go look) -- every run appends its total to a local CSV so "how much
# did we use today / this week" is answerable without opening a browser.
_UNIT_COSTS = {"search": 100, "videos": 1, "commentThreads": 1, "channels": 1}
_units_used_this_process = 0
_QUOTA_LOG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "youtube_quota_log.csv")


def get_units_used_this_process() -> int:
    return _units_used_this_process


def log_units_to_file():
    """Appends one row (date, units used by THIS process run) to the quota
    log. Call once per script run (run_daily.py calls it at the end), not
    per API call -- a day's total may span several rows if the daily
    automation runs more than once."""
    os.makedirs(os.path.dirname(_QUOTA_LOG_PATH), exist_ok=True)
    is_new = not os.path.exists(_QUOTA_LOG_PATH)
    with open(_QUOTA_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "units_used", "of_daily_free_quota"])
        writer.writerow([date.today().isoformat(), _units_used_this_process, 10000])


def _api_get(endpoint: str, params: dict) -> dict:
    global _units_used_this_process
    if not config.YOUTUBE_API_KEY:
        raise EnvironmentError("YOUTUBE_API_KEY not set in secrets .env / Doppler")
    r = requests.get(f"{API_BASE}/{endpoint}", params={**params, "key": config.YOUTUBE_API_KEY}, timeout=30)
    r.raise_for_status()
    _units_used_this_process += _UNIT_COSTS.get(endpoint, 0)
    return r.json()


def search_videos(query: str, max_videos: int = 15, min_comment_count: int = 5,
                   exclude_video_ids: set = frozenset()) -> list[dict]:
    """
    Search-text relevance alone doesn't mean there's real discussion -- a
    video can match the query and still have 3 comments. This fetches
    statistics for every search hit and ranks by commentCount, dropping
    anything below min_comment_count (comments disabled or dead videos).

    Searches a WIDE pool (up to 50, the API max per call) and only THEN
    filters out exclude_video_ids/low-comment videos and takes the top
    max_videos -- searching at exactly max_videos and filtering afterward
    would silently return fewer than requested once previously-mined videos
    got excluded.
    """
    search_data = _api_get("search", {
        "part": "snippet", "q": query, "type": "video",
        "maxResults": 50, "relevanceLanguage": "en",
    })
    candidates = {
        item["id"]["videoId"]: {"title": item["snippet"]["title"], "channel": item["snippet"]["channelTitle"]}
        for item in search_data.get("items", [])
        if item["id"]["videoId"] not in exclude_video_ids
    }
    if not candidates:
        return []

    stats_data = _api_get("videos", {"part": "statistics", "id": ",".join(candidates.keys())})

    ranked = []
    for item in stats_data.get("items", []):
        comment_count = int(item.get("statistics", {}).get("commentCount", 0))
        if comment_count < min_comment_count:
            continue
        meta = candidates[item["id"]]
        ranked.append({"video_id": item["id"], "title": meta["title"], "channel": meta["channel"], "comment_count": comment_count})

    ranked.sort(key=lambda v: v["comment_count"], reverse=True)
    return ranked[:max_videos]


def get_comments(video_id: str, video_title: str, max_comments: int = 30) -> list[dict]:
    """Ports the pagination+replies logic from youtube-comments.js, capped at max_comments."""
    comments = []
    page_token = None

    while len(comments) < max_comments:
        params = {
            "part": "snippet", "videoId": video_id, "maxResults": 100,
            "textFormat": "plainText", "order": "relevance",
        }
        if page_token:
            params["pageToken"] = page_token
        data = _api_get("commentThreads", params)

        for item in data.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            comments.append({
                "url": f"https://www.youtube.com/watch?v={video_id}&lc={item['snippet']['topLevelComment']['id']}",
                "title": video_title,
                "content": top.get("textDisplay", ""),
                "author": top.get("authorDisplayName", "unknown"),
                "engagement_score": top.get("likeCount", 0),
                "comment_count": item["snippet"].get("totalReplyCount", 0),
            })
            if len(comments) >= max_comments:
                break

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return comments


def collect(query: str, max_videos: int = 10, max_comments_per_video: int = 30) -> list[dict]:
    import db  # local import: avoids a hard dependency for callers that only need search_videos/get_comments
    known_video_ids = db.get_known_youtube_video_ids()

    items = []
    videos = search_videos(query, max_videos=max_videos, exclude_video_ids=known_video_ids)
    print(f"[youtube] {len(known_video_ids)} videos already mined (excluded from this search), "
          f"{len(videos)} new videos selected")

    for video in videos:
        try:
            comments = get_comments(video["video_id"], video["title"], max_comments=max_comments_per_video)
        except requests.RequestException as exc:
            # Covers HTTPError (comments disabled etc.) AND plain network
            # blips (ConnectionError, Timeout) -- either way, skip just this
            # one video instead of losing every comment already fetched
            # from earlier videos in this loop (that used to happen: an
            # uncaught ConnectionError on video N aborted collect() entirely
            # and threw away videos 1..N-1's results too).
            print(f"[youtube] skipping {video['video_id']} ('{video['title'][:50]}'): {exc}")
            continue

        for c in comments:
            c["source_name"] = f"youtube:{video['channel']}"
        items.extend(comments)

    return items
