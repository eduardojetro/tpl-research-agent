"""
One-off smoke test: confirms each configured secret actually works, without
ever printing the secret value itself -- only pass/fail + a harmless fact
(a row count, an account name) per service.

Run via Doppler (secrets never touch disk):
    doppler run --token %DOPPLER_TOKEN% --project tpl-brain --config dev -- python test_connections.py
or via the local .env fallback:
    python test_connections.py
"""
import config


def test_supabase():
    import db
    result = db.get_client().table("sources").select("id", count="exact").execute()
    print(f"[OK] Supabase -- connected, 'sources' table has {result.count} rows.")


def test_gemini():
    from google import genai
    client = genai.Client(api_key=config.GEMINI_API_KEY)
    response = client.models.generate_content(model=config.LLM_MODEL, contents="Reply with exactly: OK")
    print(f"[OK] Gemini ({config.LLM_MODEL}) -- responded: {response.text.strip()!r}")


def test_apify():
    from apify_client import ApifyClient
    client = ApifyClient(config.APIFY_TOKEN)
    user = client.user().get()
    print(f"[OK] Apify -- authenticated as '{user.username}'")


if __name__ == "__main__":
    checks = [("Supabase", test_supabase), ("Gemini", test_gemini), ("Apify", test_apify)]
    failures = []
    for name, fn in checks:
        try:
            fn()
        except Exception as exc:
            failures.append(name)
            print(f"[FAIL] {name} -- {exc}")

    print()
    if failures:
        print(f"{len(failures)}/{len(checks)} failed: {', '.join(failures)}")
    else:
        print("All connections OK.")
