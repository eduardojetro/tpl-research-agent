"""
Daily unattended run: for every theme in sources_seed.THEMES, in this
order: (1) cluster the existing canonical set, (2) collect a modest batch
of fresh comments (video-dedup means this won't waste quota re-mining
videos already covered), (3) extract as much of the backlog as today's
free-tier budget allows. Both clustering and extraction try Groq first,
falling back to Gemini automatically the moment Groq's daily quota is hit,
and stop cleanly (not spinning) once both are exhausted for the day.

Clustering runs FIRST, not last -- it's cheap (one LLM call per theme, not
one per item), so giving it first crack at the day's quota barely dents
what's left for extraction, whereas running it last meant it competed with
extraction for whatever quota extraction (by design) tries to fully use --
on the very first real unattended run (2026-09-25) that meant clustering
got a 429 on every single theme. New problems added by today's extraction
just wait for tomorrow's clustering pass; that's an acceptable one-day
lag, not a bug.

Meant to be run once/day via Windows Task Scheduler through
run_daily.bat, which supplies DOPPLER_TOKEN. Logs to logs/daily_<date>.log
in addition to stdout so a run can be reviewed after the fact without
needing the terminal that launched it.
"""
import sys
import os
from datetime import datetime

import clustering
import config
import pipeline
import sources_seed
from collectors import youtube_collector

PROVIDER_SEQUENCE = ["groq", "gemini"]
PER_THEME_LIMIT = 300


def cluster_theme_with_fallback(theme: str):
    # Clustering runs BEFORE extraction (see run_theme_with_fallback) and
    # gets its own provider fallback, for the same reason extraction has
    # one: whichever provider is tried first might already be at today's
    # limit. This ordering matters -- on 2026-09-25's first real daily run,
    # clustering ran AFTER extraction and hit a 429 on every single theme,
    # because extraction (by design) uses as much of the day's quota as the
    # backlog allows, leaving nothing for clustering. Clustering itself is
    # cheap (one LLM call for the theme's whole canonical set, not one per
    # item), so running it first barely dents the budget extraction needs.
    for provider in PROVIDER_SEQUENCE:
        config.LLM_PROVIDER = provider
        config.LLM_MODEL = config._DEFAULT_MODELS[provider]
        try:
            clustering.cluster_theme(theme)
            return
        except Exception as exc:
            # Try the next provider on ANY failure here, not just a quota
            # match -- observed on 2026-09-26: Groq returned
            # json_validate_failed on a single call covering 62 items
            # (consistently, across all 5 retries -- a real output-size
            # limitation, not a transient blip), and Gemini's structured
            # JSON mode handled the same input fine. Since clustering sends
            # the WHOLE canonical set in one call (can't batch without
            # missing cross-batch duplicates), a provider that struggles at
            # today's set size is worth abandoning immediately in favor of
            # the other, rather than trying to classify every possible
            # error string in advance.
            print(f"[run_daily] clustering: {provider} failed ({exc}) -- trying next provider for {theme}.")
    print(f"[run_daily] {theme} clustering: all providers failed today, will retry next run.")


class Tee:
    """Writes to both stdout and a log file, so this is reviewable after an
    unattended run without losing the live terminal output either."""
    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)
            s.flush()

    def flush(self):
        for s in self.streams:
            s.flush()


def run_theme_with_fallback(theme: str):
    # Collect ONCE per theme, not once per provider attempted -- collection
    # (Reddit via Apify, real money) has nothing to do with which LLM
    # provider extraction ends up using. Looping pipeline.run() (which
    # bundles collect+extract) per provider used to re-run Reddit collection
    # from scratch on every fallback, paying for it twice; this was found
    # and documented in TPL_Evolution_Log.md (2026-09-25 run) but not fixed
    # until now.
    print(f"\n--- {theme}: collecting ---")
    pipeline.collect_theme(theme)

    for provider in PROVIDER_SEQUENCE:
        config.LLM_PROVIDER = provider
        config.LLM_MODEL = config._DEFAULT_MODELS[provider]
        print(f"\n--- {theme} extraction via {provider} ---")
        stats = pipeline.extract_theme(theme, limit=PER_THEME_LIMIT)
        if not stats.get("quota_exhausted"):
            return  # finished this theme's available backlog (or nothing left)
        print(f"[run_daily] {provider} quota exhausted -- trying next provider for {theme}.")
    print(f"[run_daily] {theme}: all providers exhausted today, remaining backlog carries over.")


def main():
    print(f"\n{'=' * 60}\nDaily research agent run: {datetime.now().isoformat()}\n{'=' * 60}")
    for theme in sources_seed.THEMES:
        # Cluster first (cheap, one call) so it never gets starved by
        # extraction using up the day's quota -- then collect+extract with
        # whatever budget remains.
        cluster_theme_with_fallback(theme)
        try:
            run_theme_with_fallback(theme)
        except Exception as exc:
            print(f"[run_daily] {theme} FAILED (non-quota error), moving to next theme: {exc}")
    youtube_collector.log_units_to_file()
    print(f"\nYouTube quota used this run: ~{youtube_collector.get_units_used_this_process()} / 10,000 units "
          f"(logged to logs/youtube_quota_log.csv)")
    print(f"\nDaily run finished: {datetime.now().isoformat()}")


if __name__ == "__main__":
    os.makedirs("logs", exist_ok=True)
    log_path = f"logs/daily_{datetime.now().strftime('%Y-%m-%d')}.log"
    log_file = open(log_path, "a", encoding="utf-8")
    sys.stdout = Tee(sys.stdout, log_file)
    sys.stderr = Tee(sys.stderr, log_file)
    main()
