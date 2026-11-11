"""HTTP API for the live dashboard (web/).

Runs execute inside this process, so the dashboard's controls (pause, take
over, submit decision) act on the same RunControl the agent is checking.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from applypilot.applicant.run import STARTABLE, Agents, Run, run_application
from applypilot.config import Settings, load_profile, load_settings
from applypilot.llm import LLM
from applypilot.models import ApplicationStatus, Profile
from applypilot.policy import decide
from applypilot.profile_api import register_profile_routes
from applypilot.store import JobRow, Store
from applypilot.tailor import Tailor
from applypilot.writer import Critic, Writer


class StartRun(BaseModel):
    job_id: str


class ControlAction(BaseModel):
    action: Literal["pause", "take_over", "hand_back", "agent_submit", "user_submitted",
                    "open_page", "cancel"]


def _job_json(r: JobRow, settings: Settings) -> dict:
    url = r.posting.apply_url or r.url
    d = decide(url, settings.automation.accept_tos_risk_for)
    return {
        "id": r.id, "company": r.company, "title": r.title, "location": r.location,
        "url": r.url, "apply_url": url, "status": r.status, "score": r.score,
        "summary": r.assessment.summary if r.assessment else None,
        "policy": {"level": d.policy.level, "action": d.action, "warning": d.warning},
    }


def _run_json(run: Run) -> dict:
    return {
        "id": run.id, "job_id": run.job_id, "started_at": run.started_at,
        "mode": run.control.mode, "outcome": run.outcome, "warning": run.warning,
        "needs_input": run.needs_input,
    }


class AppState:
    """The loaded profile and the agents built from it. Rebuilt when the profile is saved,
    so the next run uses the new facts. A run already in progress keeps the old ones."""

    def __init__(self, profile: Profile, llm: LLM | None):
        self.llm = llm
        self.set_profile(profile)

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        if self.llm is None:
            self.agents = Agents()
        else:
            critic = Critic(self.llm, profile)
            self.agents = Agents(writer=Writer(self.llm, profile), critic=critic,
                                 tailor=Tailor(self.llm, critic, profile))


def create_app(profile_path: Path, settings_path: Path, llm: LLM | None = None) -> FastAPI:
    """`llm` is injectable for tests; otherwise one is created when an API key is set."""
    settings = load_settings(settings_path)
    store = Store(settings.database)
    runs: dict[str, Run] = {}
    tasks: set[asyncio.Task] = set()
    if llm is None and os.environ.get("ANTHROPIC_API_KEY"):
        llm = LLM(model=settings.matcher.model)
    state = AppState(load_profile(profile_path), llm)

    app = FastAPI(title="ApplyPilot")
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"], allow_headers=["*"],
    )
    shots = settings.automation.screenshots_dir
    shots.mkdir(parents=True, exist_ok=True)
    app.mount("/screenshots", StaticFiles(directory=shots), name="screenshots")
    resumes = settings.automation.resumes_dir
    resumes.mkdir(parents=True, exist_ok=True)
    app.mount("/resumes", StaticFiles(directory=resumes), name="resumes")

    def active() -> Run | None:
        return next((r for r in runs.values() if r.outcome is None), None)

    @app.get("/api/config")
    def config() -> dict:
        a = settings.automation
        return {"backend": a.backend, "novnc_url": a.novnc_url, "mode": settings.applicant.mode,
                "drafting": state.agents.writer is not None}

    @app.get("/api/status")
    async def status() -> dict:
        """Setup checklist for the first-run guide. Booleans and counts only: never a key."""
        desktop = False
        if settings.automation.backend == "docker":
            try:
                async with httpx.AsyncClient(timeout=1.5) as client:
                    desktop = (await client.get(f"{settings.automation.cdp_url}/json/version")).is_success
            except httpx.HTTPError:
                desktop = False
        else:
            desktop = True  # local backend opens its own browser per run
        counts = store.counts()
        return {
            "backend": settings.automation.backend,
            "desktop_running": desktop,
            "api_key_set": state.llm is not None,
            "example_profile": state.profile.person.email.endswith("@example.com"),
            "facts": len(state.profile.facts),
            "jobs": sum(n for s, n in counts.items() if s != ApplicationStatus.FILTERED_OUT),
            "mode": settings.applicant.mode,
        }

    @app.get("/api/jobs")
    def jobs(min_score: int | None = None, limit: int = 100) -> list[dict]:
        statuses = [s for s in ApplicationStatus if s not in (ApplicationStatus.FILTERED_OUT,)]
        return [_job_json(r, settings) for r in store.list(statuses, min_score, limit)]

    @app.get("/api/jobs/{job_id:path}/events")
    def job_events(job_id: str) -> list[dict]:
        return [e.__dict__ for e in store.events(job_id)]

    @app.post("/api/runs")
    async def start_run(body: StartRun) -> dict:
        row = store.get(body.job_id)
        if row is None:
            raise HTTPException(404, "job not found")
        if row.status not in STARTABLE:
            raise HTTPException(409, f"this job is already {row.status.replace('_', ' ')}")
        if (current := active()) is not None:
            raise HTTPException(409, f"run {current.id} is still in progress; one browser, one run")
        run = Run(job_id=body.job_id)
        runs[run.id] = run
        task = asyncio.create_task(
            run_application(run, store=store, settings=settings, profile=state.profile,
                            agents=state.agents)
        )
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return _run_json(run)

    @app.get("/api/runs")
    def list_runs() -> list[dict]:
        return [_run_json(r) for r in sorted(runs.values(), key=lambda r: r.started_at, reverse=True)]

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        return _run_json(_get(runs, run_id))

    @app.post("/api/runs/{run_id}/control")
    def control(run_id: str, body: ControlAction) -> dict:
        run = _get(runs, run_id)
        c = run.control
        simple = {"pause": c.pause, "take_over": c.take_over, "hand_back": c.hand_back,
                  "cancel": c.cancel}
        if body.action in simple:
            simple[body.action]()
        elif not c.decide(body.action):
            raise HTTPException(409, "the run isn't waiting for a decision")
        run.bus.emit("info", f"You: {body.action.replace('_', ' ')}")
        return _run_json(run)

    @app.get("/api/runs/{run_id}/events")
    async def stream(run_id: str) -> StreamingResponse:
        run = _get(runs, run_id)

        async def gen():
            async for step in run.bus.subscribe():
                yield f"data: {step.model_dump_json()}\n\n"
            yield "event: end\ndata: {}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    register_profile_routes(app, state, profile_path)
    return app


def _get(runs: dict[str, Run], run_id: str) -> Run:
    if run_id not in runs:
        raise HTTPException(404, "run not found")
    return runs[run_id]
