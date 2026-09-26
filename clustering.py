"""
Semantic clustering: turns the raw, sometimes-duplicate `problems` rows into
canonical problem clusters (TPL_Master_Plan.md section 28's Phase 1 success
criterion -- "a ranked list of meaningful problem/opportunity clusters").

Design:
  - A problem row with canonical_problem_id = NULL is a "canonical" row --
    either a singleton never merged, or the representative of a merged
    cluster. A row with canonical_problem_id SET was merged into that
    target and drops out of the canonical set (dashboard / opportunity
    ranking queries should filter on canonical_problem_id IS NULL).
  - Re-running this script is safe and cheap: it only re-clusters the
    current canonical set for a theme (db.get_unclustered_problems), not
    every row ever created. As duplicates merge, that set shrinks, so this
    stays a small LLM call even as raw_items volume grows.
  - Clustering is per seed_theme -- different themes are different topics
    by construction, so there's nothing to gain (and real cost) in
    comparing across them.
  - Each cluster's representative also gets source_platforms recomputed
    from every raw_item behind every member -- this is what makes
    cross-source frequency visible later: a problem whose raw_items span
    more than one platform (youtube + reddit, say) is cross-source
    validated, not just repeated within one platform's audience.

Usage:
    python clustering.py --theme childbirth_prep
    python clustering.py --all
"""
import argparse

import config
import db
from extraction.llm_client import cluster_core_problems
from sources_seed import THEMES


def cluster_theme(seed_theme: str) -> dict:
    problems = db.get_unclustered_problems(seed_theme)
    stats = {"theme": seed_theme, "input_count": len(problems), "clusters_formed": 0, "merged_away": 0, "errors": 0}
    if len(problems) < 2:
        print(f"[clustering] {seed_theme}: only {len(problems)} canonical problem(s), nothing to cluster.")
        return stats

    core_problems = [p["core_problem"] for p in problems]
    print(f"[clustering] {seed_theme}: clustering {len(problems)} canonical problems...")
    clusters = cluster_core_problems(core_problems)

    for group in clusters:
        member_rows = [problems[i] for i in group["member_indices"]]
        if len(member_rows) == 1:
            representative, rest = member_rows[0], []
        else:
            # Representative = strongest existing signal (highest pain_score,
            # tie-broken by highest frequency_count) so the surviving label
            # favors the clearest/most-cited phrasing when no canonical_label
            # is returned.
            member_rows.sort(key=lambda p: (p["pain_score"] or 0, p["frequency_count"] or 0), reverse=True)
            representative, rest = member_rows[0], member_rows[1:]

        total_frequency = sum(p["frequency_count"] or 1 for p in member_rows)
        max_pain = max(p["pain_score"] or 0 for p in member_rows)
        platforms = set()
        for p in member_rows:
            platforms.update(db.get_source_platforms(p["id"]))

        try:
            db.apply_cluster(
                representative_id=representative["id"],
                member_ids=[p["id"] for p in rest],
                canonical_label=group.get("canonical_label"),
                total_frequency=total_frequency,
                max_pain_score=max_pain,
                source_platforms=sorted(platforms),
            )
            stats["clusters_formed"] += 1
            stats["merged_away"] += len(rest)
            if rest:
                # Windows console codepages (cp1252) choke on some unicode the
                # LLM emits (e.g. narrow no-break spaces) -- this is a print
                # problem, not a data problem, so never let it look like the
                # merge itself failed.
                label = representative["core_problem"][:70].encode("ascii", "replace").decode("ascii")
                print(f"  merged {len(member_rows)} -> 1: \"{label}\"")
        except Exception as exc:
            stats["errors"] += 1
            print(f"  [clustering] ERROR merging cluster ({[p['id'] for p in member_rows]}): {exc}")

    print(
        f"[clustering] {seed_theme}: {stats['input_count']} -> "
        f"{stats['input_count'] - stats['merged_away']} canonical problems "
        f"({stats['merged_away']} merged away, {stats['errors']} errors)."
    )
    return stats


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--theme", help="Cluster a single seed_theme.")
    group.add_argument("--all", action="store_true", help="Cluster every theme in sources_seed.THEMES.")
    args = parser.parse_args()

    themes = list(THEMES.keys()) if args.all else [args.theme]
    for theme in themes:
        cluster_theme(theme)


if __name__ == "__main__":
    main()
