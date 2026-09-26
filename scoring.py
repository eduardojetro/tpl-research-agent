"""
Opportunity Score (master plan section 14), computed from configurable
weights (scoring_weights table) instead of a hardcoded formula, so that the
Feedback Loop (section 16) can later recalibrate these weights from real
engagement/conversion data (feedback_events table) without a code change.

frequency_count on the problems table is unbounded (raw occurrence count),
so it's capped at 10 here to stay on the same 0-10 scale as the other
factors before weighting -- same shape as the dashboard sandbox calculator.
"""
import db


def compute_opportunity_score(pain_score: int, commercial_intent_score: int,
                               solution_gap_score: int, frequency_count: int) -> float:
    weights = db.get_active_scoring_weights()
    frequency_score = min(frequency_count, 10)

    score = (
        commercial_intent_score * float(weights["weight_commercial_intent"])
        + pain_score * float(weights["weight_pain"])
        + frequency_score * float(weights["weight_frequency"])
        + solution_gap_score * float(weights["weight_solution_gap"])
    )
    return round((score / 10) * 100, 1)
