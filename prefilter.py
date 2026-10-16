"""
Pre-LLM filter for raw_items: drops what is clearly not worth a free-tier LLM
call, with a reason for every drop. Pure functions, no DB, no network.

Design rule: false drops are worse than missed drops, so every check is
conservative and when unsure the item is kept. Near-duplicate detection
adapted from last30days (MIT, mvanhorn) scripts/lib/dedupe.py.
"""
import html
import re
from typing import Dict, List, Tuple

MIN_CONTENT_CHARS = 60  # below this, a comment is almost never a real problem statement

# char-trigram Jaccard. Higher bar across authors (could be two mothers who
# genuinely wrote alike); lower bar for the same author reposting.
NEAR_DUP_THRESHOLD = 0.90
NEAR_DUP_THRESHOLD_SAME_AUTHOR = 0.80

_BOT_AUTHORS = frozenset({"automoderator", "moderator"})
_BOT_PHRASES = (
    "this action was performed automatically",
    "i am a bot",
    "your post has been removed",
    "your comment has been removed",
)
_PLACEHOLDER_AUTHORS = frozenset({"[deleted]", "[removed]", "unknown"})
_PROMO_PHRASES = (
    "link in bio", "use code", "promo code", "discount code", "dm me", "dm us",
    "follow me", "giveaway", "whatsapp", "telegram", "click the link", "shop now",
)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_WORD_RE = re.compile(r"\b\w+\b")


def _words(text: str) -> List[str]:
    return _WORD_RE.findall(text.lower())


def is_third_party_reply(content: str) -> bool:
    # Task 8 heuristic (moved here from pipeline.extract_theme): overwhelmingly
    # addressing someone else ("you/your") rather than describing a
    # first-person experience ("i/my/me") = reply or advice, not a mother's own problem.
    words = _words(content)
    you_count = words.count("you") + words.count("your") + words.count("yours")
    i_count = words.count("i") + words.count("my") + words.count("me") + words.count("mine")
    return you_count > 0 and you_count > (i_count * 2)


def _is_bot_or_mod(author: str, content: str) -> bool:
    if (author or "").strip().lower() in _BOT_AUTHORS:
        return True
    low = content.lower()
    return low.startswith("welcome to /r/") or any(p in low for p in _BOT_PHRASES)


def _is_promo_or_link_only(content: str) -> bool:
    without_urls = _URL_RE.sub(" ", content).strip()
    if _URL_RE.search(content) and len(without_urls) < MIN_CONTENT_CHARS:
        return True
    low = content.lower()
    words = _words(content)
    first_person = sum(words.count(w) for w in ("i", "my", "me", "mine", "we", "our"))
    # CTA bait only counts when there is no first-person experience around it
    return first_person == 0 and any(p in low for p in _PROMO_PHRASES)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", html.unescape(text).lower())).strip()


def _trigrams(norm: str) -> frozenset:
    if len(norm) < 3:
        return frozenset({norm}) if norm else frozenset()
    return frozenset(norm[i:i + 3] for i in range(len(norm) - 2))


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def prefilter(raw_items: List[Dict]) -> Tuple[List[Dict], List[Tuple[Dict, str]]]:
    """Return (keep, dropped) where dropped is a list of (item, reason).

    Reasons: too_short, third_party_reply, bot_or_mod, promo_or_link_only,
    exact_duplicate, near_duplicate. Order of `keep` follows the input; of two
    duplicates the earlier one is kept.
    """
    keep: List[Dict] = []
    dropped: List[Tuple[Dict, str]] = []
    seen_norm: Dict[str, Dict] = {}
    kept_grams: List[Tuple[frozenset, str]] = []  # (trigrams, author)

    for item in raw_items:
        content = (item.get("content") or "").strip()
        author = (item.get("author") or "").strip().lower()

        if len(content) < MIN_CONTENT_CHARS:
            dropped.append((item, "too_short"))
            continue
        if _is_bot_or_mod(author, content):
            dropped.append((item, "bot_or_mod"))
            continue
        if is_third_party_reply(content):
            dropped.append((item, "third_party_reply"))
            continue
        if _is_promo_or_link_only(content):
            dropped.append((item, "promo_or_link_only"))
            continue

        norm = _normalize(content)
        if norm in seen_norm:
            dropped.append((item, "exact_duplicate"))
            continue

        grams = _trigrams(norm)
        is_dup = False
        for other_grams, other_author in kept_grams:
            same_author = bool(author) and author not in _PLACEHOLDER_AUTHORS and author == other_author
            limit = NEAR_DUP_THRESHOLD_SAME_AUTHOR if same_author else NEAR_DUP_THRESHOLD
            small, big = sorted((len(grams), len(other_grams)))
            if small / big < limit:  # Jaccard can never reach the bar
                continue
            if _jaccard(grams, other_grams) >= limit:
                is_dup = True
                break
        if is_dup:
            dropped.append((item, "near_duplicate"))
            continue

        seen_norm[norm] = item
        kept_grams.append((grams, author))
        keep.append(item)

    return keep, dropped
