"""Clears whatever is left unprocessed today: newborn_sleep, postpartum_recovery,
pregnancy_first_trimester (this last one via Groq since Gemini's daily quota
is dead for today). No re-collection needed -- plenty of raw material
already sitting unprocessed from earlier runs.
"""
import pipeline

for theme, limit in [("newborn_sleep", 200), ("postpartum_recovery", 100), ("pregnancy_first_trimester", 200)]:
    print(f"\n=== {theme} (groq) ===")
    try:
        stats = pipeline.extract_theme(theme, limit=limit)
        print(f"{theme} done: {stats}")
    except Exception as exc:
        print(f"[run_groq_remaining] {theme} FAILED, moving to next theme: {exc}")
