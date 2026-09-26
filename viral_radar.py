"""
Viral radar: finds YouTube Shorts that are outperforming their channel's
usual reach, for the distribution side (Caramelo 03's REACT-without-face
strategy), NOT for problem extraction -- unrelated to raw_items/problems.

Built 2026-09-26 as an approved exception to "no new collectors this week"
(CEO + Eduardo) -- see TEAM_LOG.md / DECISIONS.md same date. Deliberately
free: official YouTube Data API only, no Apify, no paid Claude API.

Method:
  1. search.list per query below: videoDuration=short, order=viewCount,
     publishedAfter=14 days ago -- surfaces what's currently winning, not
     just what's popular all-time.
  2. videos.list for real stats (views/likes/comments), batched.
  3. channels.list for each unique channel's viewCount/videoCount, batched
     -- used as channel_avg_views. This is a MEAN, not a true median (a
     true per-channel median would need every video's view count, i.e. a
     full uploads-playlist pull per channel -- far more quota for a
     precision this first version doesn't need). outlier_score =
     video_views / channel_avg_views: a short that's pulling far more
     views than that channel's average is the actual "why did this one
     take off" signal, independent of whether the channel is big or small.
  4. Top 20 by ratio, written to Supabase (viral_radar table, schema
     combined with Caramelo 03 2026-09-26 -- shared across platforms:
     Tonho fills platform='youtube' here, Caramelo fills tiktok/instagram
     manually in the same table) and to distribution/radar/<date>.csv.

channel_median_views is actually a MEAN here (channel viewCount /
videoCount from channels.list), not a true median -- a true median needs
every video's view count per channel (a full uploads-playlist pull), far
more quota than this first version needs. Told Caramelo directly; the
column name matches the shared schema even though the YouTube rows'
values are a mean, not a median.

problem_id/answer_slug/verifiable_claim/remix_allowed are left NULL for
YouTube rows -- matching a video to a specific `problems` row or judging
"is the claim verifiable / is remixing allowed" is editorial judgment
(Caramelo/CEO triage), not something this script infers automatically.
`theme` (the query's associated seed_theme, where one applies) is the
automated link for now.

Quota cost: 5 queries x (100 search + ~1 videos + ~1 channels) ~= 510
units/day, on top of the existing daily collection run (~450-800/day
observed) -- comfortably inside the 10,000/day free cap.
"""
from datetime import datetime, timedelta, timezone

import db
from collectors.youtube_collector import _api_get

# (search query, associated seed_theme or None if it doesn't map to one of
# the 4 research themes yet -- CEO's list named these 5 specifically).
QUERIES = [
    ("fear of childbirth", "childbirth_prep"),
    ("labor induction", "childbirth_prep"),
    ("dad in the delivery room", None),
    ("epidural", "childbirth_prep"),
    ("postpartum recovery", "postpartum_recovery"),
]

MAX_RESULTS_PER_QUERY = 20
LOOKBACK_DAYS = 14
TOP_N = 20


def _search_shorts(query: str) -> list:
    published_after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _api_get("search", {
        "part": "snippet", "q": query, "type": "video",
        "videoDuration": "short", "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": MAX_RESULTS_PER_QUERY,
        "relevanceLanguage": "en",
    })
    return [
        {
            "video_id": item["id"]["videoId"],
            "title": item["snippet"]["title"],
            "channel_id": item["snippet"]["channelId"],
            "channel_title": item["snippet"]["channelTitle"],
            "published_at": item["snippet"]["publishedAt"],
        }
        for item in data.get("items", [])
    ]


def _fetch_video_stats(video_ids: list) -> dict:
    if not video_ids:
        return {}
    data = _api_get("videos", {"part": "statistics", "id": ",".join(video_ids)})
    return {
        item["id"]: {
            "view_count": int(item.get("statistics", {}).get("viewCount", 0)),
            "like_count": int(item.get("statistics", {}).get("likeCount", 0)),
            "comment_count": int(item.get("statistics", {}).get("commentCount", 0)),
        }
        for item in data.get("items", [])
    }


def _fetch_channel_avg_views(channel_ids: list) -> dict:
    if not channel_ids:
        return {}
    data = _api_get("channels", {"part": "statistics", "id": ",".join(channel_ids)})
    result = {}
    for item in data.get("items", []):
        stats = item.get("statistics", {})
        view_count = int(stats.get("viewCount", 0))
        video_count = int(stats.get("videoCount", 0)) or 1
        result[item["id"]] = view_count / video_count
    return result


def run() -> list:
    all_rows = []
    now = datetime.now(timezone.utc)

    for query, theme in QUERIES:
        try:
            videos = _search_shorts(query)
        except Exception as exc:
            print(f"[viral_radar] search FAILED for '{query}': {exc}")
            continue
        if not videos:
            continue

        stats = _fetch_video_stats([v["video_id"] for v in videos])
        channel_ids = list({v["channel_id"] for v in videos})
        channel_avgs = _fetch_channel_avg_views(channel_ids)

        for v in videos:
            s = stats.get(v["video_id"])
            if not s:
                continue
            published_at = datetime.strptime(v["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_hours = max((now - published_at).total_seconds() / 3600, 1.0)
            channel_avg = channel_avgs.get(v["channel_id"])
            ratio = (s["view_count"] / channel_avg) if channel_avg else None

            all_rows.append({
                "platform": "youtube",
                "external_id": v["video_id"],
                "url": f"https://www.youtube.com/watch?v={v['video_id']}",
                "title": v["title"],
                "creator_handle": v["channel_title"],
                "query": query,
                "theme": theme,
                "views": s["view_count"],
                "like_count": s["like_count"],
                "comment_count": s["comment_count"],
                "posted_at": v["published_at"],
                "age_hours": round(age_hours, 1),
                "views_per_hour": round(s["view_count"] / age_hours, 1),
                "channel_median_views": round(channel_avg, 1) if channel_avg else None,
                "ratio": round(ratio, 2) if ratio else None,
            })

    # Rank by ratio (nulls last), keep top N -- this is the signal CEO
    # asked for: not just "popular", but "outperforming this channel's own
    # norm" (Caramelo's triage criteria: ratio >= 5, or >=100k views on a
    # small channel -- applied by Caramelo/CEO on the stored rows, not here).
    all_rows.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    return all_rows[:TOP_N]


def store(rows: list):
    if not rows:
        print("[viral_radar] no rows to store.")
        return
    client = db.get_client()
    client.table("viral_radar").insert(rows).execute()
    print(f"[viral_radar] {len(rows)} rows inserted into Supabase.")


def write_csv(rows: list):
    import csv
    import os
    out_dir = os.path.join(os.path.dirname(__file__), "..", "distribution", "radar")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv")
    fieldnames = ["url", "title", "creator_handle", "query", "theme", "views",
                  "views_per_hour", "channel_median_views", "ratio", "age_hours"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[viral_radar] CSV written to {path}")


if __name__ == "__main__":
    top_rows = run()
    store(top_rows)
    write_csv(top_rows)
