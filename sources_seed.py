"""
Per-theme seed configuration: which subreddits to search on Reddit, and which
authority pages to fetch as evidence via Crawl4AI. All URLs below were
verified live via web search on 2026-09-20 -- do not add new ones without
verifying they resolve and are on-topic (this list becomes the Evidence
Agent's source hierarchy per master plan section 10).

Add a new theme here whenever you want the pipeline to cover a new topic
from the master plan (newborn sleep, breastfeeding, postpartum recovery...).
"""

THEMES = {
    "childbirth_prep": {
        "search_query": "preparing for labor delivery birth",
        "subreddits": ["BabyBumps", "pregnant", "predaddit", "beyondthebump"],
        "youtube_query": "how to prepare for labor and delivery first time mom",
        "evidence_urls": [
            "https://www.nhs.uk/best-start-in-life/pregnancy/preparing-for-labour-and-birth/",
            "https://www.acog.org/womens-health/pregnancy/labor-and-delivery",
            "https://www.mayoclinichealthsystem.org/childbirth-education",
            "https://www.marchofdimes.org/find-support/topics/birth/stages-labor",
            "https://www.marchofdimes.org/find-support/topics/planning-baby/your-birth-plan",
        ],
    },
    # Added 2026-09-22 to cover the site's other categories (Baby, Postpartum,
    # Pregnancy) -- childbirth_prep alone only feeds the Birth category.
    # Evidence URLs verified live via web search on 2026-09-22.
    "newborn_sleep": {
        "search_query": "newborn sleep safe sleeping tips",
        "subreddits": ["NewParents", "beyondthebump", "Mommit"],
        "youtube_query": "newborn sleep safe sleeping tips first time parents",
        "evidence_urls": [
            "https://www.nhs.uk/best-start-in-life/baby/baby-basics/newborn-and-baby-sleeping-advice-for-parents/safe-sleep-advice-for-babies/",
            "https://www.cdc.gov/sudden-infant-death/sleep-safely/index.html",
        ],
    },
    "postpartum_recovery": {
        "search_query": "postpartum recovery first weeks after birth",
        "subreddits": ["beyondthebump", "NewParents", "Mommit"],
        "youtube_query": "postpartum recovery what to expect first weeks after birth",
        "evidence_urls": [
            "https://www.mayoclinic.org/healthy-lifestyle/labor-and-delivery/in-depth/postpartum-care/art-20047233",
            "https://www.mayoclinic.org/healthy-lifestyle/labor-and-delivery/in-depth/c-section-recovery/art-20047310",
            "https://www.acog.org/clinical/clinical-guidance/committee-opinion/articles/2018/05/optimizing-postpartum-care",
        ],
    },
    "pregnancy_first_trimester": {
        "search_query": "first trimester pregnancy symptoms what to expect",
        "subreddits": ["BabyBumps", "pregnant", "predaddit"],
        "youtube_query": "first trimester pregnancy symptoms what to expect",
        "evidence_urls": [
            "https://www.mayoclinic.org/healthy-lifestyle/pregnancy-week-by-week/in-depth/pregnancy/art-20047208",
            "https://www.mayoclinic.org/healthy-lifestyle/getting-pregnant/in-depth/symptoms-of-pregnancy/art-20043853",
        ],
    },
}


def get_theme(seed_theme: str) -> dict:
    if seed_theme not in THEMES:
        raise KeyError(
            f"Unknown seed_theme '{seed_theme}'. Add it to sources_seed.THEMES first "
            f"(subreddits + verified evidence_urls) -- known themes: {list(THEMES.keys())}"
        )
    return THEMES[seed_theme]
