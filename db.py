"""
Thin wrapper around the Supabase client with the exact read/write operations
the pipeline needs. Uses the service_role key, so it bypasses RLS -- this
code must never run in a browser/client context.
"""
import re
from typing import Optional
from supabase import create_client, Client

import config

_client: Optional[Client] = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    return _client


def get_or_create_source(name: str, platform: str, url: str = None) -> str:
    db = get_client()
    existing = db.table("sources").select("id").eq("name", name).execute()
    if existing.data:
        return existing.data[0]["id"]
    created = db.table("sources").insert({"name": name, "platform": platform, "url": url}).execute()
    return created.data[0]["id"]


def insert_raw_item(source_id: str, url: str, title: str, content: str, author: str,
                     engagement_score: int, comment_count: int, seed_theme: str) -> Optional[str]:
    """Insert a raw item. Returns its id, or None if the url already exists (dedupe on unique url)."""
    db = get_client()
    existing = db.table("raw_items").select("id").eq("url", url).execute()
    if existing.data:
        return None
    result = db.table("raw_items").insert({
        "source_id": source_id,
        "url": url,
        "title": title,
        "content": content,
        "author": author,
        "engagement_score": engagement_score,
        "comment_count": comment_count,
        "seed_theme": seed_theme,
        "is_processed": False,
    }).execute()
    return result.data[0]["id"]


def get_known_youtube_video_ids() -> set:
    """
    All YouTube video IDs we've already pulled at least one comment from,
    across every theme -- used to stop the collector re-searching/re-ranking
    videos it has already mined (this is what caused the "222 fetched, 0
    new" waste when two themes' queries surfaced overlapping videos).
    Global, not per-theme: once a video's comments are in raw_items, there's
    no reason to spend YouTube quota fetching it again for a different theme.
    """
    db = get_client()
    result = db.table("raw_items").select("url").like("url", "%youtube.com/watch%").execute()
    video_ids = set()
    for row in result.data:
        match = re.search(r"[?&]v=([\w-]+)", row["url"])
        if match:
            video_ids.add(match.group(1))
    return video_ids


def get_unprocessed_raw_items(seed_theme: str, limit: int = 50):
    db = get_client()
    result = (
        db.table("raw_items")
        .select("*")
        .eq("seed_theme", seed_theme)
        .eq("is_processed", False)
        .limit(limit)
        .execute()
    )
    return result.data


def mark_raw_item_processed(raw_item_id: str, life_stage: str = None):
    db = get_client()
    payload = {"is_processed": True}
    if life_stage:
        payload["life_stage"] = life_stage
    db.table("raw_items").update(payload).eq("id", raw_item_id).execute()


def upsert_problem(core_problem: str, life_stage: str, seed_theme: str,
                    pain_score: int, llm_extraction: dict, confidence: float) -> str:
    """Dedupe on exact core_problem text; bump frequency_count if it already exists."""
    db = get_client()
    existing = db.table("problems").select("id, frequency_count").eq("core_problem", core_problem).execute()
    if existing.data:
        row = existing.data[0]
        db.table("problems").update({
            "frequency_count": row["frequency_count"] + 1,
            "pain_score": pain_score,
        }).eq("id", row["id"]).execute()
        return row["id"]

    created = db.table("problems").insert({
        "core_problem": core_problem,
        "life_stage": life_stage,
        "seed_theme": seed_theme,
        "pain_score": pain_score,
        "llm_extraction": llm_extraction,
        "extraction_confidence": confidence,
    }).execute()
    return created.data[0]["id"]


def insert_questions(problem_id: str, questions: list[str]):
    if not questions:
        return
    db = get_client()
    db.table("questions").insert([
        {"problem_id": problem_id, "question_text": q} for q in questions
    ]).execute()


def link_raw_item_problem(raw_item_id: str, problem_id: str):
    db = get_client()
    db.table("raw_item_problems").upsert({
        "raw_item_id": raw_item_id,
        "problem_id": problem_id,
    }).execute()


def get_unclustered_problems(seed_theme: str):
    """
    Problems where canonical_problem_id IS NULL for this theme -- i.e. every
    problem that is currently its own canonical/representative row (a
    singleton never yet clustered, or the representative of an earlier
    merge). This is exactly the set clustering.py re-clusters each run: it
    shrinks over time as duplicates merge, so re-running stays cheap.
    """
    db = get_client()
    result = (
        db.table("problems")
        .select("id, core_problem, frequency_count, pain_score")
        .eq("seed_theme", seed_theme)
        .is_("canonical_problem_id", "null")
        .execute()
    )
    return result.data


def get_source_platforms(problem_id: str) -> list:
    db = get_client()
    result = (
        db.table("raw_item_problems")
        .select("raw_items(sources(platform))")
        .eq("problem_id", problem_id)
        .execute()
    )
    platforms = set()
    for row in result.data:
        raw_item = row.get("raw_items") or {}
        source = raw_item.get("sources") or {}
        platform = source.get("platform")
        if platform:
            platforms.add(platform)
    return sorted(platforms)


def apply_cluster(representative_id: str, member_ids: list[str], canonical_label: Optional[str],
                   total_frequency: int, max_pain_score: int, source_platforms: list):
    """
    Commits one clustering decision: `member_ids` (which may be empty, for a
    singleton "cluster") get pointed at representative_id and are excluded
    from future canonical-set queries; representative_id absorbs their
    aggregate frequency/pain/source_platforms.
    """
    db = get_client()
    rep_update = {
        "frequency_count": total_frequency,
        "pain_score": max_pain_score,
        "source_platforms": source_platforms,
    }
    if canonical_label:
        rep_update["core_problem"] = canonical_label
    db.table("problems").update(rep_update).eq("id", representative_id).execute()
    if member_ids:
        db.table("problems").update({"canonical_problem_id": representative_id}).in_("id", member_ids).execute()


def get_active_scoring_weights() -> dict:
    db = get_client()
    result = db.table("scoring_weights").select("*").eq("is_active", True).limit(1).execute()
    if not result.data:
        raise RuntimeError("No active scoring_weights row found. Seed scoring_weights first.")
    return result.data[0]


def upsert_opportunity(problem_id: str, commercial_intent_score: int, solution_gap_score: int,
                        total_score: float, recommended_solution_type: str):
    db = get_client()
    existing = db.table("opportunities").select("id").eq("problem_id", problem_id).execute()
    payload = {
        "problem_id": problem_id,
        "commercial_intent_score": commercial_intent_score,
        "solution_gap_score": solution_gap_score,
        "total_opportunity_score": total_score,
        "recommended_solution_type": recommended_solution_type,
    }
    if existing.data:
        db.table("opportunities").update(payload).eq("id", existing.data[0]["id"]).execute()
    else:
        db.table("opportunities").insert(payload).execute()
