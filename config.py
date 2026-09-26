"""
Central config loader for the TPL Research Agent.

Two ways secrets can reach this process, both fine, no code change needed
to switch between them:

  1. Doppler (preferred) -- run the pipeline as
     `doppler run -- python run.py --theme ...`
     Doppler injects every secret as a real env var into this process; there
     is no local secrets file to load at all in that case.

  2. Local .env fallback -- a local-only file at
     C:\\Users\\eduar\\tpl-brain-secrets\\.env (deliberately outside this
     Drive-synced folder, so keys never get uploaded to Google's servers).
     Loaded automatically if it exists. See .env.example for the template.

Either way, _require() below is what actually enforces that a given secret
is present, with an error naming exactly which one is missing.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

SECRETS_PATH = Path(os.environ.get("TPL_ENV_PATH", r"C:\Users\eduar\tpl-brain-secrets\.env"))

if SECRETS_PATH.exists():
    load_dotenv(dotenv_path=SECRETS_PATH)
# else: assume secrets were injected by `doppler run` (or are already set in
# the environment some other way) -- _require() below will raise a clear
# error per-variable if that turns out not to be true.


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required env var: {name}")
    return value


SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _require("SUPABASE_SERVICE_KEY")

REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "TPL_brain research agent by u/unknown")

APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

# Official YouTube Data API v3 -- free (10,000 units/day), no OAuth, no
# anti-bot risk. Get a key at https://console.cloud.google.com/apis/credentials
# (enable "YouTube Data API v3" on the project first).
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# LLM_PROVIDER picks the backend in extraction/llm_client.py.
#   "gemini"    -- default. Free tier, no credit card -- but the DAILY quota
#                  for gemini-2.5-flash on this account is only 20
#                  requests/day (confirmed from a real 429 response on
#                  2026-09-21, quotaId GenerateRequestsPerDayPerProjectPerModel-
#                  FreeTier, quotaValue 20). Earlier docs said ~1,500/day --
#                  that number does not hold for every account/model, don't
#                  trust it without checking the actual error when it matters.
#   "groq"      -- free tier (no credit card): 30 req/min, 1,000 req/day.
#                  Model list changes on Groq's side without much notice --
#                  llama-3.3-70b-versatile (the original default here) was
#                  retired; confirm current models with
#                  `Groq(api_key=...).models.list()` before assuming a name works.
#   "anthropic" -- paid only, no free tier. Best quality, needed later once
#                  volume/quality requirements outgrow the free options.
LLM_PROVIDER = os.environ.get("TPL_LLM_PROVIDER", "gemini")

_DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "openai/gpt-oss-120b",
    "anthropic": "claude-haiku-4-5-20251001",
}
LLM_MODEL = os.environ.get("TPL_LLM_MODEL", _DEFAULT_MODELS.get(LLM_PROVIDER))

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
