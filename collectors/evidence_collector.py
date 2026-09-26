"""
Evidence collector using Crawl4AI: turns authority-site URLs into clean
Markdown for the LLM extraction step (Evidence Agent, master plan section 10).

This module does not search the web -- it fetches a curated list of URLs
(see sources_seed.py for the per-theme list). Search-driven discovery of new
authority pages is a deliberate next step, not built yet, to avoid the agent
citing pages nobody has verified are real and on-topic.
"""
import asyncio
from typing import List, Dict
from crawl4ai import AsyncWebCrawler


async def _fetch_one(crawler: AsyncWebCrawler, url: str) -> Dict:
    result = await crawler.arun(url=url)
    return {
        "url": url,
        "markdown": result.markdown if result.success else None,
        "success": result.success,
        "error": None if result.success else result.error_message,
    }


async def fetch_evidence_pages(urls: List[str]) -> List[Dict]:
    async with AsyncWebCrawler() as crawler:
        return await asyncio.gather(*[_fetch_one(crawler, u) for u in urls])


def collect(urls: List[str]) -> List[Dict]:
    """Sync wrapper -- returns [{url, markdown, success, error}, ...]."""
    return asyncio.run(fetch_evidence_pages(urls))
