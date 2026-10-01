"""Unattended backlog drain: extraction + clustering only, NEVER collection.

Collection stays paused (CEO decision, 2026-09-27) while this works through
the 1,852-item backlog already sitting in raw_items. Runs once/day via a
scheduled GitHub Actions job (.github/workflows/drain_backlog.yml), fully
unattended -- no Antigravity/chat session needed for this routine work
(CEO decision, 2026-10-01: Claude writes and owns this code; Antigravity
agents only execute explicit orders, they don't author pipeline code).

Themes run in priority order, most-neglected first (checked against
tpl-brain on 2026-10-01: postpartum_recovery was 8.8% extracted with only
2 clustered problems, vs. childbirth_prep's 100%/184 -- Eduardo is close to
that phase, so it goes first). childbirth_prep is skipped entirely: it's
already fully extracted and clustered. A theme is only left behind for the
next theme once its backlog is empty or today's free-tier quota is spent
on both providers; quota exhaustion stops the whole run (tomorrow's cron
picks up exactly where this one left off, per-theme, nothing is lost).
"""
import os
import sys
from datetime import datetime

import clustering
import config
import db
import pipeline

PROVIDER_SEQUENCE = ["groq", "gemini"]
BATCH_LIMIT = 50
THEME_PRIORITY = ["postpartum_recovery", "pregnancy_first_trimester", "newborn_sleep"]


class Tee:
    """Mirrors stdout to a log file, same pattern as run_daily.py, so an
    unattended run is reviewable after the fact."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def theme_has_backlog(theme: str) -> bool:
    return len(db.get_unprocessed_raw_items(theme, limit=1)) > 0


def extract_with_fallback(theme: str) -> dict:
    """Try extraction with Groq first, then Gemini the moment Groq's daily
    quota is hit -- same fallback order as run_daily.py. Returns the stats
    dict from whichever provider didn't report quota_exhausted, or the
    second provider's dict if both are exhausted today."""
    stats = None
    for provider in PROVIDER_SEQUENCE:
        config.LLM_PROVIDER = provider
        config.LLM_MODEL = config._DEFAULT_MODELS[provider]
        stats = pipeline.extract_theme(theme, limit=BATCH_LIMIT)
        if not stats["quota_exhausted"]:
            return stats
        print(f"[drain] {theme}: {provider} quota exhausted today, trying next provider.")
    return stats


def cluster_with_fallback(theme: str) -> None:
    for provider in PROVIDER_SEQUENCE:
        config.LLM_PROVIDER = provider
        config.LLM_MODEL = config._DEFAULT_MODELS[provider]
        try:
            clustering.cluster_theme(theme)
            return
        except Exception as exc:
            print(f"[drain] {theme}: clustering via {provider} failed ({exc}), trying next provider.")
    print(f"[drain] {theme}: clustering failed on all providers today, will retry next run.")


def drain_theme(theme: str) -> bool:
    """Returns False the instant today's quota is exhausted on both
    providers (caller should stop the whole run); True otherwise, whether
    because the theme finished draining or it had nothing to do."""
    print(f"\n=== {theme} ===")
    if not theme_has_backlog(theme):
        print(f"[drain] {theme}: already fully drained, skipping.")
        return True

    rounds = 0
    while theme_has_backlog(theme):
        rounds += 1
        stats = extract_with_fallback(theme)
        print(f"[drain] {theme} round {rounds}: {stats}")
        if stats["quota_exhausted"]:
            return False

    print(f"[drain] {theme}: backlog fully drained for now.")
    cluster_with_fallback(theme)
    return True


def main():
    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/drain_{datetime.now().strftime('%Y%m%d')}.log"
    with open(log_path, "a") as f:
        sys.stdout = Tee(sys.stdout, f)
        print(f"\n--- drain_backlog run {datetime.now().isoformat()} ---")

        for theme in THEME_PRIORITY:
            if not drain_theme(theme):
                print("[drain] Daily free-tier quota exhausted on both providers -- "
                      "stopping here, tomorrow's run resumes this theme.")
                return

        print("[drain] All priority themes fully drained -- nothing left to do today.")


if __name__ == "__main__":
    main()
