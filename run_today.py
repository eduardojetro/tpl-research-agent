"""One-off driver: clear the childbirth_prep backlog, then collect+extract
the 3 new themes -- all in one run so today's free-tier budget (Groq: 1,000
req/day, 30/min) is used efficiently instead of across several cold starts.
"""
import pipeline

print("=== Finishing childbirth_prep backlog ===")
stats = pipeline.extract_theme("childbirth_prep", limit=200)
print("childbirth_prep backlog:", stats)

for theme in ["newborn_sleep", "postpartum_recovery", "pregnancy_first_trimester"]:
    print(f"\n=== {theme} ===")
    pipeline.run(theme, limit=100)
