"""Greenhouse public Job Board API.

Docs: https://developers.greenhouse.io/job-board.html
"""

from __future__ import annotations

import httpx

from applypilot.models import JobPosting
from applypilot.sources._text import html_to_text, looks_remote, parse_iso

BASE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


async def fetch(client: httpx.AsyncClient, token: str) -> list[JobPosting]:
    resp = await client.get(BASE.format(token=token), params={"content": "true"})
    resp.raise_for_status()
    return [parse(token, job) for job in resp.json()["jobs"]]


def parse(token: str, job: dict) -> JobPosting:
    location = (job.get("location") or {}).get("name", "") or ""
    departments = job.get("departments") or []
    return JobPosting(
        source="greenhouse",
        external_id=str(job["id"]),
        company=token,
        title=job["title"].strip(),
        url=job["absolute_url"],
        apply_url=job["absolute_url"],
        location=location,
        remote=looks_remote(location),
        description=html_to_text(job.get("content"), escaped=True),
        department=departments[0]["name"] if departments else None,
        posted_at=parse_iso(job.get("first_published") or job.get("updated_at")),
    )
