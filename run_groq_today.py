"""Groq lane: clears the childbirth_prep and newborn_sleep backlogs, then
postpartum_recovery. Runs alongside run_gemini_today.py (different provider,
different theme) -- no overlap, so no wasted duplicate extraction.
"""
import pipeline

for theme, limit in [("childbirth_prep", 200), ("newborn_sleep", 250), ("postpartum_recovery", 150)]:
    print(f"\n=== {theme} (groq) ===")
    try:
        pipeline.run(theme, limit=limit)
    except Exception as exc:
        print(f"[run_groq_today] {theme} FAILED, moving to next theme: {exc}")
