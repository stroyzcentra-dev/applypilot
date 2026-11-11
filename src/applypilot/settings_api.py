"""Settings endpoints for the dashboard.

  GET    /api/settings              settings, plus whether an API key is set (never the key)
  PUT    /api/settings              validate, save (previous kept as .bak), apply now
  PUT    /api/settings/api-key      check the key with Anthropic, then store it in .env
  DELETE /api/settings/api-key      remove it from .env
  POST   /api/settings/check-board  turn a careers URL or company name into a board, and count its jobs
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

import anthropic
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from applypilot.config import Settings, save_settings
from applypilot.sources import FETCHERS, USER_AGENT

if TYPE_CHECKING:
    from applypilot.server import AppState

KEY_VAR = "ANTHROPIC_API_KEY"
_KEY_SHAPE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$")


# ---- .env --------------------------------------------------------------------------


def write_env_var(path: Path, name: str, value: str | None) -> None:
    """Set or remove one variable in a .env file, keeping every other line as it was."""
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == name:
            if value is not None and not done:
                out.append(f"{name}={value}")
                done = True
            continue
        out.append(line)
    if value is not None and not done:
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def check_key_with_anthropic(key: str) -> None:
    """Raises if Anthropic rejects the key. Lists one model: costs nothing."""
    anthropic.Anthropic(api_key=key, max_retries=0, timeout=15).models.list(limit=1)


# ---- boards ----------------------------------------------------------------------------

Source = Literal["greenhouse", "lever", "ashby"]
_HOSTS: dict[str, Source] = {
    "greenhouse.io": "greenhouse", "lever.co": "lever", "ashbyhq.com": "ashby",
}


def parse_board(text: str) -> tuple[Source | None, str]:
    """Careers link or company name -> (source, token).

    'https://boards.greenhouse.io/stripe/jobs/1' -> ('greenhouse', 'stripe');  'Stripe' -> (None, 'stripe')
    """
    text = text.strip()
    if "/" in text or "." in text:
        url = urlparse(text if "://" in text else f"https://{text}")
        host = (url.hostname or "").lower()
        source = next((s for h, s in _HOSTS.items() if host == h or host.endswith("." + h)), None)
        parts = [p for p in url.path.split("/") if p]
        if source and parts:
            # job-boards.greenhouse.io/embed/job_board?for=stripe
            if parts[0] == "embed" and "for=" in url.query:
                return source, url.query.split("for=")[1].split("&")[0].lower()
            return source, parts[0].lower()
    return None, re.sub(r"[^a-z0-9]", "", text.lower())


class BoardCheck(BaseModel):
    input: str = Field(min_length=1, max_length=300)


class BoardResult(BaseModel):
    source: Source | None
    token: str
    jobs: int
    message: str


async def find_board(text: str) -> BoardResult:
    source, token = parse_board(text)
    if not token:
        return BoardResult(source=None, token="", jobs=0, message="Enter a company name or careers link.")
    sources: list[Source] = [source] if source else ["greenhouse", "lever", "ashby"]
    async with httpx.AsyncClient(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        async def count(s: Source) -> tuple[Source, int | None]:
            try:
                return s, len(await FETCHERS[s](client, token))
            except (httpx.HTTPError, ValueError, KeyError):
                return s, None

        results = await asyncio.gather(*(count(s) for s in sources))
    found = [(s, n) for s, n in results if n is not None]
    if not found:
        if source:  # they gave a link, so we know the ATS; the board itself didn't answer
            return BoardResult(source=None, token=token, jobs=0,
                               message=f"Couldn't read a {source.title()} job board called “{token}”. "
                                       "Check the link, or open it in your browser to see where it goes.")
        return BoardResult(source=None, token=token, jobs=0,
                           message=f"No Greenhouse, Lever or Ashby job board named “{token}”. "
                                   "Paste the company's careers link instead.")
    s, n = max(found, key=lambda x: x[1])
    return BoardResult(source=s, token=token, jobs=n, message=f"Found {n} open jobs on {s.title()}.")


# ---- routes ------------------------------------------------------------------------------


class ApiKey(BaseModel):
    key: str = Field(min_length=10, max_length=300)


def register_settings_routes(app: FastAPI, state: AppState) -> None:
    def view() -> dict:
        return {"settings": state.settings.model_dump(mode="json"),
                "api_key_set": state.llm is not None}

    @app.get("/api/settings")
    def get_settings() -> dict:
        return view()

    @app.put("/api/settings")
    def put_settings(settings: Settings) -> dict:
        # Paths aren't editable from the dashboard; keep whatever the file says.
        old = state.settings
        settings = settings.model_copy(update={"database": old.database})
        settings.automation = settings.automation.model_copy(update={
            "screenshots_dir": old.automation.screenshots_dir,
            "resumes_dir": old.automation.resumes_dir,
            "cdp_url": old.automation.cdp_url,
            "novnc_url": old.automation.novnc_url,
        })
        save_settings(state.settings_path, settings)
        state.set_settings(settings)
        return view()

    @app.put("/api/settings/api-key")
    async def set_key(body: ApiKey) -> dict:
        key = body.key.strip()
        if not _KEY_SHAPE.match(key):
            raise HTTPException(422, "That doesn't look like an Anthropic API key. They start with sk-ant-.")
        checker = state.key_checker or check_key_with_anthropic
        try:
            await asyncio.to_thread(checker, key)
        except anthropic.AuthenticationError as exc:
            raise HTTPException(422, "Anthropic rejected that key. Check it was copied in full.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise HTTPException(422, "That key doesn't have access to the API.") from exc
        except anthropic.APIError as exc:
            raise HTTPException(502, "Couldn't reach Anthropic to check the key. Try again.") from exc
        write_env_var(state.env_path, KEY_VAR, key)
        os.environ[KEY_VAR] = key
        state.key_changed()
        return view()

    @app.delete("/api/settings/api-key")
    def delete_key() -> dict:
        write_env_var(state.env_path, KEY_VAR, None)
        os.environ.pop(KEY_VAR, None)
        state.key_changed()
        return view()

    @app.post("/api/settings/check-board")
    async def check_board(body: BoardCheck) -> BoardResult:
        return await find_board(body.input)
