"""Profile endpoints for the dashboard.

  GET  /api/profile                 the profile as JSON
  PUT  /api/profile                 validate, save (previous kept as .bak), reload agents
  POST /api/profile/import          resume file -> proposed facts, each checked against the resume
  POST /api/profile/refine          one fact -> suggested rewordings and follow-up questions

Imports and refinements only propose. Nothing changes until the user saves.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import anthropic
from fastapi import FastAPI, File, HTTPException, UploadFile

from applypilot.llm import LLMBadOutput, LLMRefusal
from applypilot.models import Fact, Profile
from applypilot.profile_tools import (
    FactRefiner,
    ImportResult,
    RefineResult,
    ResumeImporter,
    UnsupportedFile,
    extract_text,
    save_profile,
)

if TYPE_CHECKING:
    from applypilot.server import AppState


def register_profile_routes(app: FastAPI, state: AppState, profile_path: Path) -> None:
    def need_llm():
        if state.llm is None:
            raise HTTPException(
                503, "This needs an Anthropic API key. Add ANTHROPIC_API_KEY to .env and restart the API."
            )
        return state.llm

    async def call(fn, *args):
        try:
            return await asyncio.to_thread(fn, *args)
        except (LLMRefusal, LLMBadOutput) as exc:
            raise HTTPException(502, f"The model couldn't complete this: {exc}") from exc
        except anthropic.APIError as exc:
            msg = f"The Anthropic API returned an error: {exc.__class__.__name__}"
            raise HTTPException(502, msg) from exc

    @app.get("/api/profile")
    def get_profile() -> Profile:
        return state.profile

    @app.put("/api/profile")
    def put_profile(profile: Profile) -> Profile:
        # FastAPI has already validated it against the Profile schema (422 with field
        # paths if not), so whatever is saved here loads cleanly next time.
        save_profile(profile_path, profile)
        state.set_profile(profile)
        return profile

    @app.post("/api/profile/import")
    async def import_resume(file: Annotated[UploadFile, File()]) -> ImportResult:
        llm = need_llm()
        data = await file.read()
        try:
            text = extract_text(file.filename or "", data)
        except UnsupportedFile as exc:
            raise HTTPException(422, str(exc)) from exc
        return await call(ResumeImporter(llm).run, text, state.profile.fact_ids)

    @app.post("/api/profile/refine")
    async def refine(fact: Fact) -> RefineResult:
        return await call(FactRefiner(need_llm()).run, fact)
