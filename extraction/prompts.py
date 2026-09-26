"""
The Problem & Question Engine base prompt (TPL_Master_Plan.md section 9),
extended with the numeric fields the Opportunity Score (section 14) and the
mother_life_stage taxonomy (Supabase enum) actually need to be computed and
stored -- the original prompt text returns rich qualitative JSON but no
scores, so this version asks for both.

PROMPT_VERSION history (stored in problems.llm_extraction so every row can
be traced back to the process that produced it):
  v1 -- first production run (childbirth_prep, 2026-09-20). Gold-dataset
        audit against 37 real extractions: 19/37 (51%) false positives, 0
        measurable false negatives (the "NOT_A_PROBLEM" string-match gate
        never actually rejected anything).
  v2 -- added structural classification.is_problem + problem_type, and a
        single "current vs. resolved-in-the-past" rule. Re-tested on the
        same 37: precision 48.6% -> 86.7%, but recall dropped to 72.2% (5
        false negatives) -- the past/present rule conflated "narrated in
        the past" with "not a problem," which wrongly filtered out
        retrospective COMPLAINTS (negative, unresolved dissatisfaction)
        along with the positive testimonials it was meant to catch.
  v3 -- (this version) splits that single rule into two independent
        dimensions -- problem_status (when) and outcome_valence (how it
        turned out) -- so a past+negative account is no longer conflated
        with a past+positive one. problem_type is derived from the
        (status, valence) combination the model reports, and is_problem is
        computed in code from problem_type (see TYPES_COUNTED_AS_PROBLEM in
        pipeline.py), not trusted as a raw model-emitted boolean -- keeps
        the gate auditable against a fixed lookup instead of the model's
        own summary judgment. Also splits `coping_behavior` out from
        `core_problem`/`likely_solutions` so an action the author is
        already taking (e.g. "I'm taking notes") doesn't get mistaken for
        the solution to -- or the absence of -- the actual problem (e.g.
        "the video is too fast to follow").
"""

LIFE_STAGE_VALUES = [
    "trying_to_conceive",
    "pregnancy_trimester_1",
    "pregnancy_trimester_2",
    "pregnancy_trimester_3",
    "labor_and_delivery_prep",
    "postpartum_0_6_weeks",
    "postpartum_6w_6m",
    "postpartum_6m_1y",
    "toddler_1_3y",
    "general_unspecified",
]

SOLUTION_TYPES = [
    "free_information", "checklist", "template", "calculator", "tracker",
    "digital_product", "physical_product", "affiliate_recommendation",
    "service", "marketplace", "subscription", "membership", "software",
    "micro_saas", "ai_tool", "curated_database", "comparison_tool",
    "lead_generation", "other",
]

PROBLEM_STATUS_VALUES = [
    "current_unresolved",  # happening now, not yet solved
    "past_resolved",       # already happened, no longer open
    "future_concern",      # anticipating something that hasn't happened yet
    "not_applicable",      # no personal experience described (pure question, generic status update)
]

OUTCOME_VALENCE_VALUES = [
    "negative_unsatisfied",  # dissatisfied, it didn't go well / isn't going well
    "positive_satisfied",    # satisfied, it went well / is going well
    "neutral_ambiguous",     # no clear satisfaction signal either way
    "not_applicable",
]

# problem_type is the single field that determines whether a row becomes a
# `problems` row (see TYPES_COUNTED_AS_PROBLEM in pipeline.py). It is meant
# to be the direct result of combining problem_status x outcome_valence,
# not a separate independent guess -- the prompt below spells out that
# mapping explicitly so the model doesn't have to invent it.
PROBLEM_TYPES = [
    "problem",              # current_unresolved (any valence) or future_concern + negative
    "unresolved_complaint", # past_resolved + negative_unsatisfied -- still counts as a problem signal
    "testimonial",          # past_resolved + positive_satisfied, about her own experience
    "success_story",        # past_resolved + positive_satisfied, framed as advice/encouragement to others
    "research_signal",      # past_resolved + neutral_ambiguous (a review-like account, no clear verdict)
    "goal_desire",          # future_concern or not_applicable + no concrete difficulty described
    "solution_signal",      # describes a practice/behavior/coping action, not the problem it responds to
    "status_update",        # generic update with no expressed difficulty
    "question",             # an explicit question, regardless of status/valence
    "reflection",           # nostalgic or reflective, not problem-oriented
    "request_for_validation",  # light social "does anyone else..." with no real stakes
    "irrelevant",
    "other",
]

# The pipeline gate (pipeline.py) computes is_problem from problem_type via
# this set, instead of trusting a separate model-emitted boolean that could
# silently disagree with the model's own problem_type -- one source of
# truth, auditable against a fixed table.
TYPES_COUNTED_AS_PROBLEM = {"problem", "unresolved_complaint", "question"}

PROMPT_VERSION = "v3"

_SHARED_ANALYSIS_STEPS = """1. What is the core problem (if any)?
2. What is the mother's likely underlying need?
3. What questions would a mother with this problem naturally ask next?
4. What related questions are likely to be searched on Google or asked to an AI assistant?
5. What questions distinguish normal variation from situations that may require professional medical advice?
6. What problems commonly occur before this problem?
7. What problems commonly occur after this problem?
8. What solutions is the mother likely to consider?
9. What products, tools, services, or information could legitimately help?
10. What evidence would be required before TPL publishes advice?
11. What content formats could address the problem?
12. What information would help determine commercial intent?"""

_CLASSIFICATION_RULES = f"""Classify in two independent steps. Do this BEFORE writing core_problem, and
do not let the mere presence of words like "fear", "trauma", "pain",
"anxiety", or "difficulty" decide the outcome by themselves -- determine
whether the author is describing a difficulty that constitutes an actual
problem, a past experience already resolved, a retrospective complaint, or
simply a narrative/update.

STEP 1 -- problem_status: exactly one of {PROBLEM_STATUS_VALUES}
  Is the difficulty happening right now and unresolved, did it already
  happen and is now over, is it something anticipated about the future, or
  is there no personal difficulty described at all?

STEP 2 -- outcome_valence: exactly one of {OUTCOME_VALENCE_VALUES}
  Independently of timing: how did it turn out, or how is the author
  currently reacting to it -- dissatisfied, satisfied, or neither clearly?
  A past event can be negative (a complaint) just as easily as positive (a
  success story) -- being in the past does NOT default to positive.

STEP 3 -- problem_type: exactly one of {PROBLEM_TYPES}, derived from steps 1-2:
  - current_unresolved (any valence), or future_concern + negative_unsatisfied
      -> "problem"
  - past_resolved + negative_unsatisfied
      -> "unresolved_complaint" (SHE MOVED ON, BUT THIS IS STILL A REAL PAIN/GAP
         SIGNAL -- e.g. "they made me push before I felt ready" -- do not
         discard this just because her birth is already over)
  - past_resolved + positive_satisfied
      -> "testimonial" (about her own experience) or "success_story"
         (framed as advice/encouragement for others)
  - past_resolved + neutral_ambiguous
      -> "research_signal"
  - future_concern or not_applicable + no concrete difficulty
      -> "goal_desire"
  - describes a practice/behavior/coping action she is taking, without
    itself being the difficulty (see coping_behavior below)
      -> "solution_signal"
  - explicit question, any status/valence
      -> "question"
  - generic update, no difficulty expressed
      -> "status_update"
  - nostalgic/reflective, not problem-oriented -> "reflection"
  - light social "does anyone else..." with no real stakes -> "request_for_validation"
  - not about a motherhood/pregnancy problem at all -> "irrelevant"

IMPORTANT: a coping action (e.g. "I'm taking notes", "I've started drinking
X") is NEVER itself the problem, and its presence does NOT downgrade a real
problem into solution_signal. If the author both describes a difficulty AND
something they're doing about it, classify by the difficulty
(problem/unresolved_complaint/question) and put what they're doing into
coping_behavior, not into core_problem.

commercial_intent_score must be grounded in signals actually present in
THIS text (explicit purchase language, explicit urgency, explicit request
for a product/service/recommendation) -- not in what you imagine she might
need next. A resolved testimonial does not get a high intent score just
because the underlying topic is one where *other* people spend money."""

PROBLEM_EXPANSION_SYSTEM_PROMPT = f"""You are the TPL Problem Expansion Agent ({PROMPT_VERSION}).

Your task is to analyze a real problem expressed by a mother (in a Reddit
post/comment or similar raw text) and convert it into a structured problem
map for the Two Pink Lines (TPL) motherhood business.

DO NOT immediately create content. First determine:

{_SHARED_ANALYSIS_STEPS}

{_CLASSIFICATION_RULES}

Also determine:
- life_stage: exactly one of {LIFE_STAGE_VALUES}
- pain_score: 0-10, how strongly this problem affects the mother emotionally/practically (0 if not a problem)
- commercial_intent_score: 0-10, how close this mother is to trying/buying a solution
- solution_gap_score: 0-10, how inadequate existing solutions seem to be for this problem
- recommended_solution_type: exactly one of {SOLUTION_TYPES}

Return ONLY structured JSON (no prose, no markdown fences) with these exact keys:

{{
  "classification": {{"problem_status": "...", "outcome_valence": "...", "problem_type": "..."}},
  "core_problem": "...",
  "coping_behavior": "... or null if none described",
  "underlying_need": "...",
  "primary_question": "...",
  "associated_questions": ["..."],
  "search_questions": ["..."],
  "preceding_problems": ["..."],
  "next_problems": ["..."],
  "likely_solutions": ["..."],
  "potential_solution_categories": ["..."],
  "required_evidence": ["..."],
  "content_opportunities": ["..."],
  "commercial_intent_signals": ["..."],
  "unanswered_questions": ["..."],
  "life_stage": "...",
  "pain_score": 0,
  "commercial_intent_score": 0,
  "solution_gap_score": 0,
  "recommended_solution_type": "...",
  "confidence": 0.0
}}

Do not invent medical facts.
Do not claim that a product is appropriate without evidence.
Do not diagnose.
Flag questions requiring professional medical evaluation inside required_evidence.
core_problem/underlying_need/etc. should still describe the text even for
non-problem problem_types -- describe what it's actually about rather than
writing the literal words "not a problem".
"""

# Batch variant: analyzes many raw items in a single call. This exists purely
# for cost control -- with Apify eventually pulling large volumes of
# comments across multiple platforms, one LLM call per item does not scale
# (you pay the ~900-token system prompt every single time). Batching N items
# per call means the system prompt is paid once per batch instead of once
# per item, and with prompt caching (see llm_client.py) repeat batches in the
# same run barely pay for it at all.
PROBLEM_EXPANSION_BATCH_SYSTEM_PROMPT = f"""You are the TPL Problem Expansion Agent ({PROMPT_VERSION}).

You will receive a numbered list of raw items (Reddit posts/comments or
similar raw text from mothers). For EACH item, independently apply this
analysis:

{_SHARED_ANALYSIS_STEPS}

{_CLASSIFICATION_RULES}

Also determine per item:
- life_stage: exactly one of {LIFE_STAGE_VALUES}
- pain_score: 0-10 (0 if not a problem)
- commercial_intent_score: 0-10
- solution_gap_score: 0-10
- recommended_solution_type: exactly one of {SOLUTION_TYPES}

Return ONLY structured JSON (no prose, no markdown fences) with this exact shape:

{{
  "results": [
    {{
      "index": 0,
      "classification": {{"problem_status": "...", "outcome_valence": "...", "problem_type": "..."}},
      "core_problem": "...",
      "coping_behavior": "... or null if none described",
      "underlying_need": "...",
      "primary_question": "...",
      "associated_questions": ["..."],
      "search_questions": ["..."],
      "preceding_problems": ["..."],
      "next_problems": ["..."],
      "likely_solutions": ["..."],
      "potential_solution_categories": ["..."],
      "required_evidence": ["..."],
      "content_opportunities": ["..."],
      "commercial_intent_signals": ["..."],
      "unanswered_questions": ["..."],
      "life_stage": "...",
      "pain_score": 0,
      "commercial_intent_score": 0,
      "solution_gap_score": 0,
      "recommended_solution_type": "...",
      "confidence": 0.0
    }}
  ]
}}

"index" must match the item's number in the input list (0-based), and
"results" must contain exactly one object per input item, in any order --
including non-problem items; do not omit them.

Do not invent medical facts.
Do not claim that a product is appropriate without evidence.
Do not diagnose.
Flag questions requiring professional medical evaluation inside required_evidence.
core_problem/underlying_need/etc. should still describe the item even for
non-problem problem_types -- describe what it's actually about rather than
writing the literal words "not a problem".
"""

# --------------------------------------------------------- Clustering ------

# CLUSTERING_PROMPT_VERSION history:
#   v1 -- first version (2026-09-24). Groups core_problem strings within a
#         single seed_theme into canonical clusters. Deliberately does NOT
#         see underlying_need, pain_score, or raw source text -- core_problem
#         is already the model's own paraphrase of "what this is really
#         about", so re-showing raw text would just re-litigate extraction
#         instead of judging similarity between two already-clean statements.
CLUSTERING_PROMPT_VERSION = "v1"

CLUSTERING_SYSTEM_PROMPT = f"""You are the TPL Problem Clustering Agent ({CLUSTERING_PROMPT_VERSION}).

You receive a numbered list of short "core_problem" statements. Each one was
independently extracted from a different piece of real content (a YouTube
comment, a Reddit post, etc.) by an earlier extraction step. Several of them
may describe the exact same underlying problem or need in different words.

Your job: group the indices into clusters, where every item in a cluster
describes the SAME underlying problem or need -- not just the same broad
topic. Two items belong in the same cluster only if a person who has that
problem solved would also consider the other problem solved.

Be conservative. When in doubt, keep items separate -- a false merge hides
real signal (it makes two distinct pain points look like one, understating
how often the bigger one actually happens), while a missed merge only costs
a little redundancy that a future clustering pass can still catch. Every
item must appear in exactly one cluster, including items with no match
(their own cluster of size 1).

For every cluster with 2 or more members, also write a "canonical_label":
one clean, specific sentence describing the shared problem, written at the
same level of specificity as the inputs -- not vaguer. Prefer reusing the
clearest member's own wording over inventing new phrasing.

Input format:
0. <core_problem text>
1. <core_problem text>
...

Output STRICT JSON, no markdown fences, in exactly this shape:
{{
  "clusters": [
    {{"member_indices": [0, 3, 7], "canonical_label": "..."}},
    {{"member_indices": [1], "canonical_label": null}},
    ...
  ]
}}

member_indices must be integers referencing the input list. Every input
index must appear in exactly one cluster's member_indices, with no
duplicates and no omissions. canonical_label is null for size-1 clusters.
"""
