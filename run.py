"""
CLI entrypoint.

Usage:
    python run.py --theme childbirth_prep
    python run.py --theme childbirth_prep --limit 20
    python run.py --theme childbirth_prep --evidence-only
"""
import argparse
import pipeline


def main():
    parser = argparse.ArgumentParser(description="Run the TPL Research Agent for one seed theme.")
    parser.add_argument("--theme", required=True, help="Seed theme key from sources_seed.THEMES")
    parser.add_argument("--limit", type=int, default=50, help="Max raw_items to extract per run")
    parser.add_argument("--evidence-only", action="store_true", help="Only fetch evidence pages, skip Reddit/extraction")
    args = parser.parse_args()

    if args.evidence_only:
        pages = pipeline.fetch_theme_evidence(args.theme)
        for page in pages:
            status = "OK" if page["success"] else f"FAILED: {page['error']}"
            print(f"[{status}] {page['url']} ({len(page['markdown'] or '')} chars)")
        return

    pipeline.run(args.theme, limit=args.limit)


if __name__ == "__main__":
    main()
