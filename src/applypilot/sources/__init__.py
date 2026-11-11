"""Job sources. Each one reads a public ATS job board API and returns JobPostings."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from applypilot.config import Sources
from applypilot.models import JobPosting
from applypilot.sources import ashby, greenhouse, lever

log = logging.getLogger(__name__)

Fetcher = Callable[[httpx.AsyncClient, str], Awaitable[list[JobPosting]]]

FETCHERS: dict[str, Fetcher] = {
    "greenhouse": greenhouse.fetch,
    "lever": lever.fetch,
    "ashby": ashby.fetch,
}

USER_AGENT = "ApplyPilot/0.1 (+https://github.com/stroyzcentra-dev/applypilot)"


@dataclass
class ScoutResult:
    postings: list[JobPosting] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


async def scout(sources: Sources, *, concurrency: int = 6, timeout: float = 20.0) -> ScoutResult:
    """Fetch every configured board concurrently. One bad board never sinks the sweep."""
    boards = [(name, token) for name in FETCHERS for token in getattr(sources, name)]
    result = ScoutResult()
    sem = asyncio.Semaphore(concurrency)

    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:

        async def run(name: str, token: str) -> None:
            async with sem:
                try:
                    result.postings.extend(await FETCHERS[name](client, token))
                except (httpx.HTTPError, ValueError, KeyError) as exc:
                    log.warning("source %s:%s failed: %s", name, token, exc)
                    result.errors[f"{name}:{token}"] = str(exc) or type(exc).__name__

        await asyncio.gather(*(run(n, t) for n, t in boards))

    return result
