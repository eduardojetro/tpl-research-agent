"""Plain-assert tests for prefilter.py. Run: python test_prefilter.py"""
from prefilter import prefilter

GENUINE = ("I am 34 weeks and my heartburn is so bad I cannot sleep lying down, "
           "my doctor said nothing helps and I am exhausted.")

ITEMS = [
    {"id": "1", "author": "mom_a", "content": GENUINE},
    # exact duplicate (different author, case/punctuation noise)
    {"id": "2", "author": "mom_b", "content": GENUINE.upper().replace(",", "")},
    # near duplicate, same author reposting with a small edit
    {"id": "3", "author": "mom_a", "content": GENUINE + " Please help!"},
    {"id": "4", "author": "mom_c", "content": "so tired"},
    {"id": "5", "author": "x", "content": "You should really talk to your doctor, you know your body and you "
                                          "can ask your midwife about it, you will be fine."},
    {"id": "6", "author": "AutoModerator", "content": "Welcome to /r/pregnant! This is a space for everyone, "
                                                        "please read the rules before posting."},
    {"id": "7", "author": "spam", "content": "Best prenatal vitamins, use code MAMA20 and visit the shop, link in bio for the discount!"},
    {"id": "8", "author": "link", "content": "Read the article at: https://www.parents.com/baby/sleep/basics/the-ferber-method-explained/"},
    {"id": "9", "author": "mom_d", "content": "I had a c section and it hurt for weeks, I could barely lift my baby out of the crib."},
    # similar but opposite meaning, different author: must NOT be merged
    {"id": "10", "author": "mom_e", "content": "I had a c section and it did not hurt at all, I could lift my baby out of the crib."},
    {"id": "11", "author": "mom_f", "content": None},
]


def reasons(dropped):
    return {item["id"]: reason for item, reason in dropped}


keep, dropped = prefilter(ITEMS)
kept_ids = [i["id"] for i in keep]
why = reasons(dropped)
print("kept:", kept_ids)
print("dropped:", why)

assert why["2"] == "exact_duplicate"
assert why["3"] == "near_duplicate"
assert why["4"] == "too_short"
assert why["5"] == "third_party_reply"
assert why["6"] == "bot_or_mod"
assert why["7"] == "promo_or_link_only"
assert why["8"] == "promo_or_link_only"
assert why["11"] == "too_short"  # None content must not crash
assert kept_ids == ["1", "9", "10"], kept_ids  # genuine problems kept, first of each dup kept
assert len(keep) + len(dropped) == len(ITEMS)
assert prefilter([]) == ([], [])

# first-person problem that mentions "you" once must be kept
mixed = [{"id": "m", "author": "z", "content": "I wish you could see how swollen my feet are, I cannot fit into any of my shoes at 36 weeks."}]
assert [i["id"] for i in prefilter(mixed)[0]] == ["m"]

print("ALL OK")
