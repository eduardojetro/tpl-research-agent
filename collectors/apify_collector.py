"""
Generic Apify collector -- reserved for platforms with no free official API
(Instagram, TikTok). Reddit stays on PRAW (collectors/reddit_collector.py),
which is free and official; no reason to spend Apify credits there.

Free plan = $5/month in credits, does NOT roll over. That means the real
danger isn't "which actor is cheapest", it's a misconfigured run with no
cap silently burning the whole month in one go. Every call through here
enforces a hard `max_items` ceiling -- there is no "unlimited" mode.

Actor + item-mapping choice is deliberately NOT hardcoded yet: different
actors return different JSON shapes, and picking one blind risks silently
mismapping fields. Once you have an APIFY_TOKEN, run a manual test with
a tiny max_items (10-20) against a candidate actor, inspect the raw output
with a throwaway script, THEN write its item_mapper here. Candidates found
with current pricing (Sept 2026):
  - Instagram comments: apidojo/instagram-comments-scraper (~$0.50/1k) or
    the official apify/instagram-comment-scraper (~$2.30/1k, more likely
    to stay maintained/stable -- worth the premium if budget allows).
"""
from typing import Callable, Dict, List
from apify_client import ApifyClient

import config

_client = None


def get_client() -> ApifyClient:
    global _client
    if _client is None:
        if not config.APIFY_TOKEN:
            raise EnvironmentError("APIFY_TOKEN not set in secrets .env / Doppler")
        _client = ApifyClient(config.APIFY_TOKEN)
    return _client


def estimate_cost_usd(n_items: int, price_per_1000: float) -> float:
    return round((n_items / 1000) * price_per_1000, 4)


def collect_via_actor(actor_id: str, run_input: Dict, item_mapper: Callable[[Dict], Dict],
                       max_items: int, price_per_1000: float, source_name: str) -> List[Dict]:
    """
    actor_id: e.g. "apidojo/instagram-comments-scraper"
    run_input: actor-specific input dict -- MUST include that actor's own
               result-limiting field (e.g. "resultsLimit") set to max_items;
               this function does not trust the actor to self-limit.
    item_mapper: converts one raw Apify dataset item into our raw_item shape
                 {url, title, content, author, engagement_score, comment_count}
    max_items: hard ceiling -- items beyond this are dropped even if the
               actor returned more.
    price_per_1000: the actor's advertised pay-per-result price, used only
               to print an estimate before you commit to a real run.
    """
    estimated = estimate_cost_usd(max_items, price_per_1000)
    print(f"[apify] About to run '{actor_id}', max_items={max_items}, "
          f"estimated cost ~${estimated} of the $5/month free credit.")

    client = get_client()
    run = client.actor(actor_id).call(run_input=run_input)

    items = []
    for raw_item in client.dataset(run.default_dataset_id).iterate_items():
        if len(items) >= max_items:
            break
        mapped = item_mapper(raw_item)
        mapped["source_name"] = source_name
        items.append(mapped)

    actual_cost = estimate_cost_usd(len(items), price_per_1000)
    print(f"[apify] Done. {len(items)} items collected, ~${actual_cost} spent.")
    return items
