"""
Health check for the research pipeline: `python health_check.py`
(locally: `doppler run --project tpl-brain --config dev -- python health_check.py`).

Prints one row per source/job with ok / degraded / broken / unknown and a
reason. Always exits 0 unless the script itself crashes -- it reports, it
does not gate. Written after the viral radar crashed silently on 2026-10-06
and 10-07 and nobody noticed until a GitHub email.

Status model (ok/degraded/broken + a reason per source) adapted from
last30days (MIT, mvanhorn) lib/health.py and lib/doctor.py -- only the idea,
none of the code; this checks our own sources, not its plugin registry.

Secrets come from the environment only (Doppler); values are never printed.
Deliberately does not import config.py, which raises on a missing secret --
a health check has to survive exactly that.
"""
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OK, DEGRADED, BROKEN, UNKNOWN = "ok", "degraded", "broken", "unknown"
WORKFLOWS = ["drain_backlog.yml", "viral_radar.yml"]  # daily.yml is paused on purpose (2026-09-27)
RADAR_DEGRADED_DAYS = 2
RADAR_BROKEN_DAYS = 3
WORKFLOW_STALE_HOURS = 36  # both are daily crons


def _supabase(path, extra_headers=None, params=None):
    url = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path
    key = os.environ["SUPABASE_SERVICE_KEY"]
    headers = {"apikey": key, "Authorization": f"Bearer {key}", **(extra_headers or {})}
    return requests.get(url, headers=headers, params=params, timeout=20)


def check_supabase():
    if not (os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY")):
        return BROKEN, "SUPABASE_URL / SUPABASE_SERVICE_KEY not set"
    r = _supabase("raw_items", params={"select": "id", "limit": 1})
    if r.status_code == 200:
        return OK, "raw_items readable"
    return BROKEN, f"HTTP {r.status_code}: {r.text[:120]}"


def check_youtube():
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return BROKEN, "YOUTUBE_API_KEY not set"
    # videos.list on one id costs 1 quota unit; search.list would cost 100.
    r = requests.get("https://www.googleapis.com/youtube/v3/videos",
                     params={"part": "id", "id": "dQw4w9WgXcQ", "key": key}, timeout=20)
    if r.status_code == 200:
        return OK, "key valid, quota available"
    try:
        reason = r.json()["error"]["errors"][0]["reason"]
    except Exception:
        reason = r.text[:100]
    if reason in ("quotaExceeded", "rateLimitExceeded", "dailyLimitExceeded"):
        return BROKEN, f"quota exhausted today ({reason})"
    return BROKEN, f"HTTP {r.status_code}: {reason}"


def check_env_key(name):
    # Presence only: validating Groq/Gemini keys would spend free-tier quota.
    return (OK, "key present (not validated)") if os.environ.get(name) else (BROKEN, f"{name} not set")


def check_workflow(workflow):
    if not shutil.which("gh"):
        return UNKNOWN, "gh CLI not installed"
    try:
        p = subprocess.run(
            ["gh", "run", "list", "--workflow", workflow, "--limit", "3",
             "--json", "conclusion,status,createdAt,url"],
            capture_output=True, text=True, timeout=30, cwd=HERE)
    except Exception as exc:
        return UNKNOWN, f"gh failed: {exc}"
    if p.returncode != 0:
        return UNKNOWN, f"gh error: {(p.stderr or p.stdout).strip()[:120]}"
    runs = json.loads(p.stdout or "[]")
    if not runs:
        return UNKNOWN, "no runs found"
    last = runs[0]
    age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last["createdAt"].replace("Z", "+00:00"))).total_seconds() / 3600
    outcome = last["conclusion"] or last["status"]
    if outcome == "failure":
        return BROKEN, f"last run failed {age_h:.0f}h ago {last['url']}"
    if outcome not in ("success", "in_progress", "queued"):
        return DEGRADED, f"last run {outcome} {age_h:.0f}h ago {last['url']}"
    if age_h > WORKFLOW_STALE_HOURS:
        return DEGRADED, f"last run {outcome} but {age_h:.0f}h ago (daily cron missed?)"
    if any(r["conclusion"] == "failure" for r in runs[1:]):
        return DEGRADED, f"last run {outcome} {age_h:.0f}h ago, but one of the 2 before it failed"
    return OK, f"last run {outcome} {age_h:.0f}h ago"


def check_radar_freshness():
    r = _supabase("viral_radar", params={"select": "found_date", "order": "found_date.desc", "limit": 1})
    if r.status_code != 200:
        return BROKEN, f"HTTP {r.status_code}: {r.text[:120]}"
    rows = r.json()
    if not rows:
        return BROKEN, "viral_radar table is empty"
    last = datetime.strptime(rows[0]["found_date"][:10], "%Y-%m-%d").date()
    age = (datetime.now(timezone.utc).date() - last).days
    if age >= RADAR_BROKEN_DAYS:
        return BROKEN, f"last found_date {last} ({age} days ago)"
    if age >= RADAR_DEGRADED_DAYS:
        return DEGRADED, f"last found_date {last} ({age} days ago)"
    return OK, f"last found_date {last} ({age} days ago)"


def check_backlog():
    """One row per seed_theme; backlog is normal while drain_backlog works it
    down, so this is informational (ok) -- the drain workflow row says if the
    drain itself is stuck."""
    try:
        from sources_seed import THEMES
        themes = list(THEMES)
    except Exception:
        themes = []
    rows = []
    for theme in themes:
        r = _supabase("raw_items", {"Prefer": "count=exact", "Range": "0-0"},
                      {"select": "id", "seed_theme": f"eq.{theme}", "is_processed": "eq.false"})
        if r.status_code not in (200, 206):
            rows.append((f"backlog:{theme}", BROKEN, f"HTTP {r.status_code}"))
            continue
        count = int(r.headers.get("Content-Range", "*/0").split("/")[-1])
        rows.append((f"backlog:{theme}", OK, f"{count} unprocessed raw_items"))
    return rows


def _safe(name, fn):
    try:
        status, reason = fn()
    except Exception as exc:
        status, reason = BROKEN, f"check crashed: {type(exc).__name__}: {str(exc)[:80]}"
    return [(name, status, reason)]


def main():
    results = []
    results += _safe("supabase", check_supabase)
    results += _safe("youtube_api", check_youtube)
    results += _safe("groq_key", lambda: check_env_key("GROQ_API_KEY"))
    results += _safe("gemini_key", lambda: check_env_key("GEMINI_API_KEY"))
    for wf in WORKFLOWS:
        results += _safe(f"workflow:{wf}", lambda wf=wf: check_workflow(wf))
    results += _safe("viral_radar_fresh", check_radar_freshness)
    try:
        results += check_backlog()
    except Exception as exc:
        results.append(("backlog", BROKEN, f"check crashed: {type(exc).__name__}: {str(exc)[:80]}"))

    width = max(len(n) for n, _, _ in results)
    print(f"{'SOURCE'.ljust(width)}  {'STATUS':8}  REASON")
    for name, status, reason in results:
        print(f"{name.ljust(width)}  {status.upper():8}  {reason}")
    bad = [n for n, s, _ in results if s in (BROKEN, DEGRADED)]
    print(f"\n{len(results) - len(bad)}/{len(results)} ok" + (f"; needs attention: {', '.join(bad)}" if bad else ""))


if __name__ == "__main__":
    main()
