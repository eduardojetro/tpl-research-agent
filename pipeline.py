"""
TPL Research Agent orchestrator.

Given a seed_theme (must be registered in sources_seed.THEMES):

  1. COLLECT   -- pull posts+top comments from Reddit (PRAW) and comments
                  from YouTube (official Data API v3), store as raw_items
                  (deduped by URL). Each source is best-effort: a missing
                  credential just skips that source, see collect_theme().
  2. FILTER    -- drop items too short to plausibly contain a real problem
                  BEFORE spending any LLM tokens on them (free, no API call).
  3. EXTRACT   -- run the Problem & Question Engine prompt (LLM provider set
                  by TPL_LLM_PROVIDER, default Gemini free tier) in batches
                  (see extraction/llm_client.py) on the remaining unprocessed
                  raw_items.
  4. STORE     -- upsert into problems/questions/raw_item_problems, dedupe
                  problems by core_problem text, bump frequency_count on repeats.
  5. SCORE     -- compute the Opportunity Score from the active weights and
                  upsert into opportunities.

Cost control is deliberate here, not an afterthought: once Apify adds
TikTok/Instagram, volume goes from "a couple hundred Reddit posts" to
thousands of comments across many videos, and both the Apify bill and the
LLM bill scale with how much raw content reaches step 3. The MIN_CONTENT_CHARS
filter and llm_client's batching+prompt-caching exist specifically so that
scaling up sources later doesn't scale up cost linearly.

Evidence grounding (Crawl4AI against NHS/ACOG/Mayo Clinic/etc, see
collectors/evidence_collector.py + sources_seed.py) is fetched separately via
`fetch_theme_evidence` -- it is not yet wired into the extraction call. Next
step once this base loop is validated: pass the fetched evidence markdown
into the LLM call so required_evidence claims are grounded instead of
LLM-generated from parametric knowledge.
"""
from typing import Dict, List

import db
import scoring
import sources_seed
from collectors import reddit_collector, apify_reddit_collector, youtube_collector, evidence_collector
from extraction import llm_client, prompts

MIN_CONTENT_CHARS = 60  # below this, a comment is almost never a real problem statement


def _store_items(items: List[Dict], seed_theme: str, platform: str) -> int:
    inserted = 0
    for item in items:
        source_id = db.get_or_create_source(item["source_name"], platform=platform)
        raw_item_id = db.insert_raw_item(
            source_id=source_id,
            url=item["url"],
            title=item["title"],
            content=item["content"],
            author=item["author"],
            engagement_score=item["engagement_score"],
            comment_count=item["comment_count"],
            seed_theme=seed_theme,
        )
        if raw_item_id:
            inserted += 1
    return inserted


def collect_theme(seed_theme: str) -> int:
    """
    Best-effort across every collector this theme has config for: a missing
    credential (Reddit app still pending approval, YouTube key not set yet,
    etc.) skips that one source with a clear log line instead of failing
    the whole run -- so the pipeline keeps working with whatever sources
    are actually available today.
    """
    theme = sources_seed.get_theme(seed_theme)
    total_inserted = 0

    try:
        reddit_items = reddit_collector.collect(query=theme["search_query"], subreddits=theme["subreddits"])
        n = _store_items(reddit_items, seed_theme, platform="reddit")
        total_inserted += n
        print(f"[collect] reddit (PRAW): {n} new raw_items inserted ({len(reddit_items)} fetched, rest were dupes).")
    except Exception as exc:
        # PRAW app still pending Reddit's approval (as of 2026-09-25) --
        # fall back to the Apify actor instead of losing this source
        # entirely. Once PRAW is approved, this branch simply stops
        # triggering (reddit_collector.collect succeeds above) -- no code
        # change needed to "switch back".
        print(f"[collect] reddit (PRAW) SKIPPED ({exc}), falling back to Apify...")
        try:
            apify_items = apify_reddit_collector.collect(query=theme["search_query"], subreddits=theme["subreddits"])
            n = _store_items(apify_items, seed_theme, platform="reddit")
            total_inserted += n
            print(f"[collect] reddit (Apify): {n} new raw_items inserted ({len(apify_items)} fetched, rest were dupes).")
        except Exception as exc2:
            print(f"[collect] reddit (Apify) SKIPPED: {exc2}")

    if "youtube_query" in theme:
        try:
            yt_items = youtube_collector.collect(query=theme["youtube_query"])
            n = _store_items(yt_items, seed_theme, platform="youtube")
            total_inserted += n
            print(f"[collect] youtube: {n} new raw_items inserted ({len(yt_items)} fetched, rest were dupes).")
        except Exception as exc:
            print(f"[collect] youtube SKIPPED: {exc}")

    return total_inserted


def _store_extraction(raw_item: Dict, extraction: dict, seed_theme: str):
    # is_problem is computed here from problem_type against a fixed lookup
    # (prompts.TYPES_COUNTED_AS_PROBLEM), not read as a separate
    # model-emitted boolean -- one source of truth, auditable, can't
    # silently disagree with the model's own problem_type.
    problem_type = extraction.get("classification", {}).get("problem_type")
    is_problem = problem_type in prompts.TYPES_COUNTED_AS_PROBLEM
    if not is_problem or not extraction.get("core_problem"):
        db.mark_raw_item_processed(raw_item["id"])
        return None

    problem_id = db.upsert_problem(
        core_problem=extraction["core_problem"],
        life_stage=extraction.get("life_stage", "general_unspecified"),
        seed_theme=seed_theme,
        pain_score=extraction.get("pain_score", 0),
        llm_extraction=extraction,
        confidence=extraction.get("confidence", 0.0),
    )

    db.insert_questions(problem_id, extraction.get("associated_questions", []))
    db.link_raw_item_problem(raw_item["id"], problem_id)
    db.mark_raw_item_processed(raw_item["id"], life_stage=extraction.get("life_stage"))

    problem_row = db.get_client().table("problems").select("frequency_count").eq("id", problem_id).execute().data[0]

    total_score = scoring.compute_opportunity_score(
        pain_score=extraction.get("pain_score", 0),
        commercial_intent_score=extraction.get("commercial_intent_score", 0),
        solution_gap_score=extraction.get("solution_gap_score", 0),
        frequency_count=problem_row["frequency_count"],
    )

    db.upsert_opportunity(
        problem_id=problem_id,
        commercial_intent_score=extraction.get("commercial_intent_score", 0),
        solution_gap_score=extraction.get("solution_gap_score", 0),
        total_score=total_score,
        recommended_solution_type=extraction.get("recommended_solution_type", "other"),
    )

    return problem_id


def _chunk(items: List, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def extract_theme(seed_theme: str, limit: int = 50) -> Dict[str, int]:
    raw_items = db.get_unprocessed_raw_items(seed_theme, limit=limit)

    keep, skipped = [], 0
    for raw_item in raw_items:
        if len(raw_item.get("content") or "") < MIN_CONTENT_CHARS:
            db.mark_raw_item_processed(raw_item["id"])
            skipped += 1
        else:
            keep.append(raw_item)

    print(f"[extract] {seed_theme}: {skipped} raw_items skipped pre-LLM (too short), {len(keep)} sent to extraction.")

    problems_found = 0
    batches_run = 0
    quota_exhausted = False
    for batch in _chunk(keep, llm_client.BATCH_SIZE):
        try:
            extractions = llm_client.expand_problems_batch(batch)
            batches_run += 1
        except Exception as exc:
            error_text = str(exc)
            print(f"[extract] BATCH FAILED ({len(batch)} items): {exc}")
            if any(marker in error_text for marker in llm_client._DAILY_QUOTA_MARKERS):
                # Every remaining batch on this provider will fail the exact
                # same way today -- stop burning time/requests confirming
                # that N more times. The caller (run_daily.py) uses
                # quota_exhausted to decide whether to retry this theme on
                # a different provider right away.
                print(f"[extract] {seed_theme}: daily quota hit, stopping this theme's extraction for now "
                      f"({len(keep) - batches_run * llm_client.BATCH_SIZE} items still unprocessed).")
                quota_exhausted = True
                break
            continue

        for raw_item, extraction in zip(batch, extractions):
            try:
                result = _store_extraction(raw_item, extraction, seed_theme)
                if result:
                    problems_found += 1
                    print(f"[extract] {raw_item['url']} -> problem found")
                else:
                    print(f"[extract] {raw_item['url']} -> not a problem, skipped")
            except Exception as exc:
                print(f"[extract] FAILED storing {raw_item['url']}: {exc}")

    print(f"[extract] {seed_theme}: {batches_run} LLM batch calls, {problems_found} problems found out of {len(keep)} analyzed.")
    return {
        "skipped_pre_llm": skipped, "analyzed": len(keep), "problems_found": problems_found,
        "llm_calls": batches_run, "quota_exhausted": quota_exhausted,
    }


def fetch_theme_evidence(seed_theme: str):
    theme = sources_seed.get_theme(seed_theme)
    return evidence_collector.collect(theme["evidence_urls"])


def run(seed_theme: str, limit: int = 50):
    collect_theme(seed_theme)
    stats = extract_theme(seed_theme, limit=limit)
    print(f"[run] Done. {stats}")
    return stats
