"""
Calls an LLM to run the Problem & Question Engine prompt, on whichever
provider config.LLM_PROVIDER selects.

Three backends, same interface (expand_problems_batch), so switching
provider is a one-line env var change, not a code change:

  - "gemini" (default): Google AI Studio free tier -- no credit card,
    ~1,500 requests/day on Gemini 2.5 Flash. Best default for this stage:
    $0 cost while validating the pipeline, and Flash is a solid, current
    general-purpose model for structured extraction.
  - "groq": free tier -- no credit card, 30 req/min / 1,000 req/day, serves
    open-weight models (Llama 3.3 70B by default here). Roughly comparable
    quality to Gemini Flash for structured JSON extraction, useful as a
    second free option if Gemini's daily cap is hit mid-run.
  - "anthropic": paid only (no free tier), Claude Haiku 4.5. Kept as the
    fallback for when quality/consistency needs to go up a notch -- has
    prompt caching wired in since every request there costs real money.

All three are asked for JSON-only output; each backend's helper is
responsible for actually enforcing/parsing that.

Cost/scale note: batching (see pipeline.py, BATCH_SIZE items per call)
matters more than which of these three you pick -- it's what keeps the
per-item system-prompt overhead from being paid over and over as Apify
brings in higher volume later.
"""
import json
import re
import time
from typing import List, Dict

import config
from extraction.prompts import (
    PROBLEM_EXPANSION_SYSTEM_PROMPT,
    PROBLEM_EXPANSION_BATCH_SYSTEM_PROMPT,
    CLUSTERING_SYSTEM_PROMPT,
)

BATCH_SIZE = 10

# Real failure modes observed in production, both retried here:
#   - Rate limits (Gemini's daily quota, Groq's per-minute) -- wait the
#     provider's suggested retryDelay, or a longer default.
#   - Plain network blips (Windows WinError 10054 connection resets, DNS
#     hiccups, Groq's occasional "json_validate_failed" on a malformed
#     generation) -- these are usually transient and clear on their own
#     within a few seconds; a short retry recovers most of them for free.
MAX_RETRIES = 5
DEFAULT_RETRY_SECONDS = 20
CONNECTION_RETRY_SECONDS = 5
_RATE_LIMIT_MARKERS = ("429", "RESOURCE_EXHAUSTED", "503", "UNAVAILABLE")
_CONNECTION_MARKERS = ("Connection error", "ConnectError", "WinError", "json_validate_failed", "Timeout")

# A DAILY quota error will not clear up within this process's lifetime no
# matter how long we wait -- retrying it just burns minutes (5 attempts x
# ~60s suggested delay each) before failing anyway. Fail immediately instead.
# Covers both Gemini's daily REQUEST quota ("...PerDay...") and Groq's daily
# TOKEN quota ("tokens per day (TPD)" -- seen at 200k tokens/day on the free
# tier, hit for real after ~370 items processed in one day). Either way, the
# caller should move on to the next batch/theme/provider right away.
_DAILY_QUOTA_MARKERS = ("PerDay", "GenerateRequestsPerDayPerProjectPerModel", "tokens per day", "TPD")


def _retry_delay_seconds(error_text: str) -> float:
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+(?:\.\d+)?)s", error_text)
    if match:
        return float(match.group(1)) + 2
    if any(marker in error_text for marker in _CONNECTION_MARKERS):
        return CONNECTION_RETRY_SECONDS
    return DEFAULT_RETRY_SECONDS


def _with_retry(fn, *args, **kwargs):
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            error_text = str(exc)
            if any(marker in error_text for marker in _DAILY_QUOTA_MARKERS):
                print("[llm_client] daily quota exhausted for this provider -- not retrying today.")
                raise
            is_retryable = any(marker in error_text for marker in _RATE_LIMIT_MARKERS + _CONNECTION_MARKERS)
            if not is_retryable or attempt == MAX_RETRIES:
                raise
            delay = _retry_delay_seconds(error_text)
            print(f"[llm_client] transient error (attempt {attempt}/{MAX_RETRIES}), waiting {delay:.0f}s...")
            time.sleep(delay)
            last_exc = exc
    raise last_exc

_anthropic_client = None
_gemini_client = None
_groq_client = None


def _extract_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(json)?|```$", "", text, flags=re.MULTILINE).strip()
    return json.loads(text)


def _format_batch_input(items: List[Dict]) -> str:
    blocks = []
    for i, item in enumerate(items):
        blocks.append(
            f"### ITEM {i}\nSOURCE: {item.get('url', '')}\nTITLE: {item.get('title', '')}\n"
            f"RAW TEXT:\n{item.get('content', '')}"
        )
    return "\n\n".join(blocks)


_MISSING_RESULT_STUB = {
    "classification": {"is_problem": False, "problem_type": "irrelevant"},
    "core_problem": "(no extraction returned by the model for this item)",
    "confidence": 0.0,
}


def _results_from_batch_json(parsed: dict, n_items: int) -> List[dict]:
    results_by_index = {r["index"]: r for r in parsed.get("results", [])}
    return [
        results_by_index.get(i, _MISSING_RESULT_STUB)
        for i in range(n_items)
    ]


# ---------------------------------------------------------------- Gemini --

def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        if not config.GEMINI_API_KEY:
            raise EnvironmentError(
                "GEMINI_API_KEY not set in secrets .env. "
                "Get a free key at https://aistudio.google.com/apikey"
            )
        from google import genai
        _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gemini_client


def _expand_batch_gemini(items: List[Dict]) -> List[dict]:
    from google.genai import types
    client = _get_gemini_client()
    response = client.models.generate_content(
        model=config.LLM_MODEL,
        contents=_format_batch_input(items),
        config=types.GenerateContentConfig(
            system_instruction=PROBLEM_EXPANSION_BATCH_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )
    parsed = _extract_json(response.text)
    return _results_from_batch_json(parsed, len(items))


# ------------------------------------------------------------------ Groq --

def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        if not config.GROQ_API_KEY:
            raise EnvironmentError(
                "GROQ_API_KEY not set in secrets .env. "
                "Get a free key at https://console.groq.com/keys"
            )
        from groq import Groq
        _groq_client = Groq(api_key=config.GROQ_API_KEY)
    return _groq_client


def _expand_batch_groq(items: List[Dict]) -> List[dict]:
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROBLEM_EXPANSION_BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": _format_batch_input(items)},
        ],
    )
    parsed = _extract_json(response.choices[0].message.content)
    return _results_from_batch_json(parsed, len(items))


# ------------------------------------------------------------ Anthropic --

def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        if not config.ANTHROPIC_API_KEY:
            raise EnvironmentError("ANTHROPIC_API_KEY not set in secrets .env")
        from anthropic import Anthropic
        _anthropic_client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _anthropic_client


def _expand_batch_anthropic(items: List[Dict]) -> List[dict]:
    client = _get_anthropic_client()
    response = client.messages.create(
        model=config.LLM_MODEL,
        max_tokens=800 * len(items),
        system=[
            {
                "type": "text",
                "text": PROBLEM_EXPANSION_BATCH_SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": _format_batch_input(items)}],
    )
    parsed = _extract_json(response.content[0].text)
    return _results_from_batch_json(parsed, len(items))


def expand_problem(raw_text: str, source: str, title: str = "") -> dict:
    """Single-item convenience wrapper, for debugging -- real runs use expand_problems_batch."""
    return expand_problems_batch([{"content": raw_text, "url": source, "title": title}])[0]


_BACKENDS = {
    "gemini": _expand_batch_gemini,
    "groq": _expand_batch_groq,
    "anthropic": _expand_batch_anthropic,
}


def expand_problems_batch(items: List[Dict]) -> List[dict]:
    """
    items: list of dicts with at least 'content', optionally 'url'/'title'
           (same shape as a raw_items row).
    Returns a list of extraction dicts, same length and order as `items`.
    """
    if not items:
        return []
    backend = _BACKENDS.get(config.LLM_PROVIDER)
    if backend is None:
        raise ValueError(f"Unknown TPL_LLM_PROVIDER '{config.LLM_PROVIDER}', expected one of {list(_BACKENDS)}")
    return _with_retry(backend, items)


# --------------------------------------------------------- Clustering -----

def _format_clustering_input(core_problems: List[str]) -> str:
    return "\n".join(f"{i}. {text}" for i, text in enumerate(core_problems))


def _cluster_max_tokens(n_items: int) -> int:
    # Clustering's output isn't bounded by a fixed per-item schema (a
    # cluster's member_indices list can be long) -- scale generously with
    # input size. Found necessary 2026-09-26: Groq's default max_tokens
    # (unset here originally) silently truncated the JSON for a 62-item
    # clustering call, which then failed to parse as invalid JSON -- looked
    # like a model/prompt failure but was actually just an output cutoff.
    return 500 + 80 * n_items


def _cluster_gemini(core_problems: List[str]) -> dict:
    from google.genai import types
    client = _get_gemini_client()
    response = client.models.generate_content(
        model=config.LLM_MODEL,
        contents=_format_clustering_input(core_problems),
        config=types.GenerateContentConfig(
            system_instruction=CLUSTERING_SYSTEM_PROMPT,
            response_mime_type="application/json",
            max_output_tokens=_cluster_max_tokens(len(core_problems)),
        ),
    )
    return _extract_json(response.text)


def _cluster_groq(core_problems: List[str]) -> dict:
    client = _get_groq_client()
    response = client.chat.completions.create(
        model=config.LLM_MODEL,
        response_format={"type": "json_object"},
        max_tokens=_cluster_max_tokens(len(core_problems)),
        messages=[
            {"role": "system", "content": CLUSTERING_SYSTEM_PROMPT},
            {"role": "user", "content": _format_clustering_input(core_problems)},
        ],
    )
    return _extract_json(response.choices[0].message.content)


def _cluster_anthropic(core_problems: List[str]) -> dict:
    client = _get_anthropic_client()
    response = client.messages.create(
        model=config.LLM_MODEL,
        max_tokens=_cluster_max_tokens(len(core_problems)),
        system=[{"type": "text", "text": CLUSTERING_SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _format_clustering_input(core_problems)}],
    )
    return _extract_json(response.content[0].text)


_CLUSTER_BACKENDS = {
    "gemini": _cluster_gemini,
    "groq": _cluster_groq,
    "anthropic": _cluster_anthropic,
}


def cluster_core_problems(core_problems: List[str]) -> List[dict]:
    """
    core_problems: ordered list of core_problem strings (already a single
    seed_theme's worth -- clustering across themes isn't meaningful, they're
    different topics by construction).
    Returns a list of {"member_indices": [...], "canonical_label": str|None}.
    Every index appears in exactly one cluster; the caller does not need to
    handle missing/duplicate indices itself (validated + repaired here).
    """
    if not core_problems:
        return []
    if len(core_problems) == 1:
        return [{"member_indices": [0], "canonical_label": None}]
    backend = _CLUSTER_BACKENDS.get(config.LLM_PROVIDER)
    if backend is None:
        raise ValueError(f"Unknown TPL_LLM_PROVIDER '{config.LLM_PROVIDER}', expected one of {list(_CLUSTER_BACKENDS)}")
    parsed = _with_retry(backend, core_problems)

    n = len(core_problems)
    seen = set()
    clusters = []
    for group in parsed.get("clusters", []):
        members = [i for i in group.get("member_indices", []) if isinstance(i, int) and 0 <= i < n and i not in seen]
        if not members:
            continue
        seen.update(members)
        clusters.append({"member_indices": members, "canonical_label": group.get("canonical_label")})
    # Repair: any index the model dropped becomes its own singleton cluster
    # rather than silently losing that problem from the canonical set.
    for i in range(n):
        if i not in seen:
            clusters.append({"member_indices": [i], "canonical_label": None})
    return clusters
