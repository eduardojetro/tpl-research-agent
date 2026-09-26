"""Gemini lane: today's fresh 20-request/day quota, spent entirely on
pregnancy_first_trimester (the theme that got nothing done yesterday due to
a DNS outage). Runs alongside run_groq_today.py.
"""
import pipeline

try:
    pipeline.run("pregnancy_first_trimester", limit=190)
except Exception as exc:
    print(f"[run_gemini_today] FAILED: {exc}")
