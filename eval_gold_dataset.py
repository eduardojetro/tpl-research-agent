"""
Regression test against the human-audited gold dataset (childbirth_prep,
2026-09-21): re-runs extraction on the SAME 37 original comments using the
CURRENT prompt version, and compares classification.is_problem against the
human_is_problem column a person filled in by hand.

This does not touch Supabase -- it is a pure prompt/model regression test,
meant to be re-run every time the prompt changes to check whether accuracy
against this fixed reference set actually improved (or regressed).

Usage:
    python eval_gold_dataset.py path/to/*_human_audit.csv
"""
import csv
import sys
from extraction import llm_client
from extraction.prompts import PROMPT_VERSION, TYPES_COUNTED_AS_PROBLEM


def load_gold_dataset(path: str):
    """
    Reads columns by name for the fields our own export produced
    (problem_id, source_text, source_url -- these are early columns and
    unaffected), but reads the 8 human_* audit columns positionally from
    the END of each row (human_is_problem, ..., human_notes, in that exact
    order) instead of by name. Spreadsheet round-tripping (Excel/Sheets
    save) left a block of blank padding columns between
    recommended_solution_type and human_is_problem in at least one export
    of this file, which silently breaks name-based lookup (DictReader maps
    the padding, not the real answers) without breaking the file open --
    positional reading from the end is robust to that because nothing
    follows human_notes.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        raw_rows = list(reader)

    idx = {name: header.index(name) for name in ("problem_id", "source_text", "source_url")}

    rows = []
    for raw in raw_rows:
        if len(raw) < 8:
            continue
        (human_is_problem, _specificity, _pain, _intent, _content,
         _commercial, human_duplicate_of_id, _notes) = raw[-8:]
        human_raw = human_is_problem.strip().lower()
        if human_raw not in ("true", "false"):
            continue  # skip rows the human hasn't audited yet
        rows.append({
            "problem_id": raw[idx["problem_id"]],
            "content": raw[idx["source_text"]],
            "url": raw[idx["source_url"]],
            "human_is_problem": human_raw == "true",
            "human_duplicate_of_id": human_duplicate_of_id.strip(),
        })
    return rows


def run_eval(path: str):
    gold = load_gold_dataset(path)
    print(f"Loaded {len(gold)} human-audited rows from {path}")
    print(f"Testing prompt {PROMPT_VERSION}\n")

    results = []
    for batch_start in range(0, len(gold), llm_client.BATCH_SIZE):
        batch = gold[batch_start:batch_start + llm_client.BATCH_SIZE]
        extractions = llm_client.expand_problems_batch(batch)
        for row, extraction in zip(batch, extractions):
            classification = extraction.get("classification", {})
            predicted_type = classification.get("problem_type", "?")
            predicted = predicted_type in TYPES_COUNTED_AS_PROBLEM
            results.append({
                **row,
                "predicted_is_problem": predicted,
                "predicted_type": predicted_type,
                "predicted_status": classification.get("problem_status", "?"),
                "predicted_valence": classification.get("outcome_valence", "?"),
            })

    tp = [r for r in results if r["human_is_problem"] and r["predicted_is_problem"]]
    fp = [r for r in results if not r["human_is_problem"] and r["predicted_is_problem"]]
    fn = [r for r in results if r["human_is_problem"] and not r["predicted_is_problem"]]
    tn = [r for r in results if not r["human_is_problem"] and not r["predicted_is_problem"]]

    print(f"{'problem_id':<12} {'human':<7} {'pred':<7} {'status':<18} {'valence':<20} {'type':<22} match")
    print("-" * 110)
    for r in results:
        match = "OK" if r["human_is_problem"] == r["predicted_is_problem"] else "MISS"
        print(f"{r['problem_id'][:10]:<12} {str(r['human_is_problem']):<7} {str(r['predicted_is_problem']):<7} "
              f"{r['predicted_status']:<18} {r['predicted_valence']:<20} {r['predicted_type']:<22} {match}")

    total = len(results)
    accuracy = (len(tp) + len(tn)) / total if total else 0
    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 0
    recall = len(tp) / (len(tp) + len(fn)) if (tp or fn) else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0

    print(f"\n--- {PROMPT_VERSION} vs human audit (n={total}) ---")
    print(f"True positive  (human=problem,  pred=problem): {len(tp)}  {[r['problem_id'][:8] for r in tp]}")
    print(f"False positive (human=not,      pred=problem): {len(fp)}  {[r['problem_id'][:8] for r in fp]}")
    print(f"False negative (human=problem,  pred=not):     {len(fn)}  {[r['problem_id'][:8] for r in fn]}")
    print(f"True negative  (human=not,      pred=not):     {len(tn)}  {[r['problem_id'][:8] for r in tn]}")
    print(f"Accuracy:  {accuracy:.1%}")
    print(f"Precision: {precision:.1%}")
    print(f"Recall:    {recall:.1%}")
    print(f"F1:        {f1:.1%}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python eval_gold_dataset.py path/to/*_human_audit.csv")
        sys.exit(1)
    run_eval(sys.argv[1])
