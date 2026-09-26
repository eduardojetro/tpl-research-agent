"""
Reddit collector using PRAW (official, free Reddit API).

Requires a Reddit "script" app: https://www.reddit.com/prefs/apps
(the reddit_solver.py script from the earlier session automates filling
that form -- you still need to solve the captcha and click "create app"
yourself, then copy the client_id/secret into the secrets .env).

Apify remains the plan for TikTok/Instagram, which have no usable free
official search API. Reddit has one, so we use it directly and save the
Apify credits for the platforms that actually need them.
"""
from typing import List, Dict
import praw

import config


def get_reddit_client() -> praw.Reddit:
    if not (config.REDDIT_CLIENT_ID and config.REDDIT_CLIENT_SECRET):
        raise EnvironmentError(
            "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET not set. "
            "Create a Reddit script app and fill them into the secrets .env."
        )
    return praw.Reddit(
        client_id=config.REDDIT_CLIENT_ID,
        client_secret=config.REDDIT_CLIENT_SECRET,
        user_agent=config.REDDIT_USER_AGENT,
    )


def collect(query: str, subreddits: List[str], limit_per_subreddit: int = 25,
            top_comments_per_post: int = 5) -> List[Dict]:
    """
    Search each subreddit for `query`, and for each matching post pull the
    post body plus its top comments (this is where mothers phrase problems
    in natural language, not just in the title).

    Returns a list of dicts: url, title, content, author, engagement_score,
    comment_count -- shaped to feed straight into db.insert_raw_item.
    """
    reddit = get_reddit_client()
    items = []

    for subreddit_name in subreddits:
        subreddit = reddit.subreddit(subreddit_name)
        for submission in subreddit.search(query, sort="relevance", time_filter="year", limit=limit_per_subreddit):
            submission.comments.replace_more(limit=0)
            top_comments = sorted(submission.comments.list(), key=lambda c: c.score, reverse=True)[:top_comments_per_post]
            comments_text = "\n\n".join(
                f"[comment, score={c.score}] {c.body}" for c in top_comments if hasattr(c, "body")
            )

            content = (submission.selftext or "").strip()
            if comments_text:
                content = f"{content}\n\n--- TOP COMMENTS ---\n\n{comments_text}".strip()

            if not content:
                continue

            items.append({
                "url": f"https://www.reddit.com{submission.permalink}",
                "title": submission.title,
                "content": content,
                "author": str(submission.author) if submission.author else "[deleted]",
                "engagement_score": submission.score,
                "comment_count": submission.num_comments,
                "source_name": f"r/{subreddit_name}",
            })

    return items
