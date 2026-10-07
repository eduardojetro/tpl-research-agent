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

Quota cost: 7 queries x (100 search + ~1 videos + ~1 channels) ~= 714
units/day, on top of the existing daily collection run (~450-800/day
observed) -- comfortably inside the 10,000/day free cap.

2026-09-26 v3 (same day as v1/v2, per Caramelo 03's continued triage):
window widened back to 30 days for his modeling method (negative controls
need a wider net than the react-now use case) with a per-row react_ready
flag (age_hours <= 14 days) so "actionable this week" doesn't need a
second search pass; real language detection (langdetect) added on top of
the script-range + defaultLanguage checks, closing the French/Spanish/
transliterated-Hindi gap those two couldn't catch; and a views floor
(10k) for anything with ratio >= 1.5, while low-ratio videos stay in
regardless of size since Caramelo's method needs those as negative
controls, not just winners.
"""
from datetime import datetime, timedelta, timezone

import db
from collectors.youtube_collector import _api_get

# Reprioritized 2026-09-26 per Caramelo 03's triage of the first radar run:
# 9 of 20 results were non-English, and 60% came from postpartum_recovery
# (a theme with no published answer yet -- pure backlog for Tiao 02, not
# usable today). Queries below favor the themes that already have a
# published answer (fear/anxiety of birth, unmedicated birth, birth prep),
# with postpartum kept but deliberately just one query so it competes for
# fewer of the top-N slots instead of dominating them.
QUERIES = [
    ("scared of giving birth", "childbirth_prep"),
    ("birth anxiety", "childbirth_prep"),
    ("labor fear", "childbirth_prep"),
    ("unmedicated birth tips", "childbirth_prep"),
    ("birth partner tips", None),
    ("labor prep exercises", "childbirth_prep"),
    ("postpartum recovery", "postpartum_recovery"),
]

MAX_RESULTS_PER_QUERY = 20
# 30 days, not 7 -- Caramelo's modeling method (negative controls included)
# wants a wider net than the react-now use case. react_ready (below) is the
# per-row flag that narrows back down to "actionable this week" without
# needing a second, quota-costing search pass.
LOOKBACK_DAYS = 30
REACT_READY_HOURS = 336  # 14 days -- react_strategy_v1.md §4: <=14d actionable, <=7d ideal (Caramelo filters "ideal" himself off age_hours)
TOP_N = 20
# Below this view count, a high ratio is more likely small-channel noise
# than a real viral signal -- EXCEPT ratio < 1.5 rows, which Caramelo wants
# to keep regardless of size: his modeling method needs negative controls
# (same-niche videos that did NOT take off) to compare against, and a tiny
# ratio is exactly what a control looks like.
MIN_VIEWS_FOR_TOP = 10_000
CONTROL_CANDIDATE_RATIO_CEILING = 1.5

# Unicode script ranges for the languages Caramelo actually saw contaminating
# the first run (Hindi/Devanagari, Arabic, Bengali, Tamil) -- a title with any
# character in these ranges is dropped outright. This does NOT catch French/
# Spanish (still Latin script); those are only discouraged by
# relevanceLanguage+regionCode below, not guaranteed excluded -- a real
# per-title language detector would be needed to close that gap, not added
# here to keep this free and dependency-light.
_NON_LATIN_SCRIPT_RANGES = [
    (0x0900, 0x097F),  # Devanagari (Hindi)
    (0x0600, 0x06FF),  # Arabic
    (0x0980, 0x09FF),  # Bengali
    (0x0B80, 0x0BFF),  # Tamil
]


def _looks_non_english_script(title: str) -> bool:
    return any(
        lo <= ord(ch) <= hi
        for ch in title
        for lo, hi in _NON_LATIN_SCRIPT_RANGES
    )


def _looks_non_english(title: str, description: str) -> bool:
    """Real language detection (langdetect, free, offline, no API) -- closes
    the gap the script-range check above can't: French/Spanish/transliterated
    Hindi all use Latin characters, so they pass the script check but aren't
    English. Caramelo flagged exactly these three in the first two runs.
    Title alone is often too short for langdetect to be reliable, so this
    runs on title+description together. Detection failure (langdetect raises
    on very short/ambiguous text) is treated as "unknown", not "non-English"
    -- same fail-open philosophy as the defaultLanguage check: only drop on
    a CONFIRMED non-en result."""
    try:
        from langdetect import detect
        return detect(f"{title} {description}".strip()) != "en"
    except Exception:
        return False


def _search_shorts(query: str) -> list:
    published_after = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _api_get("search", {
        "part": "snippet", "q": query, "type": "video",
        "videoDuration": "short", "order": "viewCount",
        "publishedAfter": published_after,
        "maxResults": MAX_RESULTS_PER_QUERY,
        "relevanceLanguage": "en",
        "regionCode": "US",  # a single call only supports one region -- US chosen as the larger English-speaking audience; add a second GB-region pass later if this isn't enough
    })
    results = []
    for item in data.get("items", []):
        title = item["snippet"]["title"]
        if _looks_non_english_script(title):
            continue
        results.append({
            "video_id": item["id"]["videoId"],
            "title": title,
            "channel_id": item["snippet"]["channelId"],
            "channel_title": item["snippet"]["channelTitle"],
            "published_at": item["snippet"]["publishedAt"],
        })
    return results


def _fetch_video_stats(video_ids: list) -> dict:
    if not video_ids:
        return {}
    # part=snippet,statistics (not just statistics) -- snippet carries
    # defaultLanguage/defaultAudioLanguage, a more reliable English/non-English
    # signal than guessing from the title's script when the uploader set it
    # (many don't -- absent means "unknown", not "non-English", so it's only
    # used to DROP on a confirmed non-en value, never to require its presence).
    data = _api_get("videos", {"part": "snippet,statistics", "id": ",".join(video_ids)})
    result = {}
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        lang = snippet.get("defaultLanguage") or snippet.get("defaultAudioLanguage")
        if lang and not lang.lower().startswith("en"):
            continue  # confirmed non-English by the uploader's own metadata -- drop
        result[item["id"]] = {
            "view_count": int(item.get("statistics", {}).get("viewCount", 0)),
            "like_count": int(item.get("statistics", {}).get("likeCount", 0)),
            "comment_count": int(item.get("statistics", {}).get("commentCount", 0)),
            "tags": snippet.get("tags", []),
            "description": snippet.get("description", ""),
        }
    return result


def _fetch_transcript(video_id: str):
    """Free, no API key (not the official YouTube Data API -- scrapes the
    public timedtext endpoint) -- so it costs no quota, only latency. Many
    videos have transcripts disabled or no English auto-caption; that's a
    normal, expected outcome here, not an error worth logging loudly."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=("en",))
        return " ".join(snippet["text"] for snippet in fetched.to_raw_data())
    except Exception:
        return None


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
            if _looks_non_english(v["title"], s["description"]):
                continue
            published_at = datetime.strptime(v["published_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age_hours = max((now - published_at).total_seconds() / 3600, 1.0)
            channel_avg = channel_avgs.get(v["channel_id"])
            ratio = (s["view_count"] / channel_avg) if channel_avg else None

            # Views floor: below MIN_VIEWS_FOR_TOP a high ratio is usually
            # small-channel noise, not a modelable viral -- but a LOW ratio
            # is kept regardless of size, because Caramelo's method needs
            # negative controls (same-niche videos that did not take off).
            if s["view_count"] < MIN_VIEWS_FOR_TOP and (ratio is None or ratio >= CONTROL_CANDIDATE_RATIO_CEILING):
                continue

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
                "tags": s["tags"],
                "description": s["description"],
                "posted_at": v["published_at"],
                "age_hours": round(age_hours, 1),
                "react_ready": age_hours <= REACT_READY_HOURS,
                "views_per_hour": round(s["view_count"] / age_hours, 1),
                "channel_median_views": round(channel_avg, 1) if channel_avg else None,
                "ratio": round(ratio, 2) if ratio else None,
            })

    # Rank by ratio (nulls last), keep top N -- this is the signal CEO
    # asked for: not just "popular", but "outperforming this channel's own
    # norm" (Caramelo's triage criteria: ratio >= 5, or >=100k views on a
    # small channel -- applied by Caramelo/CEO on the stored rows, not here).
    all_rows.sort(key=lambda r: (r["ratio"] is None, -(r["ratio"] or 0)))
    top_rows = all_rows[:TOP_N]

    # Transcript only for the top 10, per CEO's request -- it's free (no
    # YouTube quota, separate library) but adds real latency (one HTTP
    # fetch per video), so it's not worth paying for all TOP_N=20.
    for row in top_rows[:10]:
        row["transcript"] = _fetch_transcript(row["external_id"])
    for row in top_rows[10:]:
        row["transcript"] = None

    return top_rows


def store(rows: list):
    if not rows:
        print("[viral_radar] no rows to store.")
        return
    client = db.get_client()
    # Postgres rejects an upsert batch that hits the same unique key twice
    # ("ON CONFLICT DO UPDATE command cannot affect row a second time"),
    # which crashed the radar on 2026-10-06 and 10-07 when one video came
    # back from two queries. Keep the last row per key.
    rows = list({(r["platform"], r["external_id"]): r for r in rows}.values())
    # upsert, not insert -- the same video can legitimately resurface across
    # different queries (e.g. matches both "birth anxiety" and "labor fear"),
    # and running this script twice in one day is meant to refresh stats
    # (views climb), not error out on the (platform, external_id, day)
    # unique index.
    client.table("viral_radar").upsert(rows, on_conflict="platform,external_id,found_date").execute()
    print(f"[viral_radar] {len(rows)} rows upserted into Supabase.")


def write_csv(rows: list):
    import csv
    import os
    out_dir = os.path.join(os.path.dirname(__file__), "..", "distribution", "radar")
    out_dir = os.path.abspath(out_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.csv")
    fieldnames = ["url", "title", "creator_handle", "query", "theme", "views",
                  "views_per_hour", "channel_median_views", "ratio", "age_hours", "react_ready"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[viral_radar] CSV written to {path}")


if __name__ == "__main__":
    top_rows = run()
    store(top_rows)
    write_csv(top_rows)
