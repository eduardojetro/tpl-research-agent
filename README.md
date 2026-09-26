# TPL Research Agent

Pipeline: Reddit (PRAW) -> Problem & Question Engine (LLM, batched) -> Supabase
(`problems`, `questions`, `opportunities`). Crawl4AI evidence fetch exists
but is not yet wired into scoring (see `pipeline.py` docstring).

The LLM step defaults to **Gemini 2.5 Flash on the free tier** (no credit
card, ~1,500 requests/day) -- see `extraction/llm_client.py` for the
provider comparison. Switch provider with `TPL_LLM_PROVIDER=groq|anthropic`
in the secrets `.env`, no code change needed.

## One-time setup

1. Create the secrets file (outside this Drive-synced folder, so keys never
   get uploaded to Google's servers):

   ```
   copy .env.example C:\Users\eduar\tpl-brain-secrets\.env
   ```

   Then fill in, in `C:\Users\eduar\tpl-brain-secrets\.env`:
   - `SUPABASE_SERVICE_KEY` -- Supabase dashboard > tpl-brain project > Settings > API > service_role secret
   - `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` -- create a "script" app at https://www.reddit.com/prefs/apps
     (the old `robo_reddit.bat` / `reddit_solver.py` in the parent folder automates filling that form;
     you still do the captcha + click "create app" yourself)
   - `GEMINI_API_KEY` -- free key, no credit card, from https://aistudio.google.com/apikey
     (this is the only LLM key needed by default -- leave GROQ_API_KEY/ANTHROPIC_API_KEY blank
     unless you change TPL_LLM_PROVIDER)
   - `APIFY_TOKEN` -- not needed for the first run (Reddit-only), needed later for TikTok/Instagram

2. Install dependencies (already done once on this machine):
   ```
   pip install -r requirements.txt
   ```

## Run the first test (childbirth prep)

```
python run.py --theme childbirth_prep
```

This searches r/BabyBumps, r/pregnant, r/predaddit, r/beyondthebump, stores
raw posts+comments, runs the extraction prompt on each, and writes
problems/questions/opportunities into Supabase.

To add a new topic from the master plan (newborn sleep, breastfeeding...),
add an entry to `THEMES` in `sources_seed.py` with subreddits + verified
evidence URLs, then run `python run.py --theme <your_new_key>`.
