"""HTTP API for the dashboard (web/).

Everything the dashboard can change lives in AppState: the profile, the
settings, the API key and the agents built from them. Saving any of them takes
effect for the next action without a restart. Runs execute inside this process,
so the dashboard's controls act on the same RunControl the agent is checking.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from applypilot.applicant.run import STARTABLE, Agents, Run, run_application
from applypilot.config import Settings, ensure_config_files, load_profile, load_settings
from applypilot.llm import LLM
from applypilot.models import ApplicationStatus, Profile
from applypilot.policy import decide
from applypilot.store import JobRow, Store
from applypilot.tailor import Tailor
from applypilot.writer import Critic, Writer

REPO = Path(__file__).resolve().parents[2]


class StartRun(BaseModel):
    job_id: str


class ControlAction(BaseModel):
    action: Literal["pause", "take_over", "hand_back", "agent_submit", "user_submitted",
                    "open_page", "cancel"]


def job_json(r: JobRow, settings: Settings) -> dict:
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
    """Profile, settings, API key and agents. Each setter rebuilds what depends on it;
    a run already in progress keeps the objects it started with."""

    def __init__(self, profile_path: Path, settings_path: Path, env_path: Path,
                 llm: LLM | None = None, key_checker: Callable[[str], None] | None = None):
        self.profile_path, self.settings_path, self.env_path = profile_path, settings_path, env_path
        self.settings = load_settings(settings_path)
        self.profile = load_profile(profile_path)
        self.store = Store(self.settings.database)
        self._fixed_llm = llm  # tests inject a fake model; it then ignores key changes
        self.key_checker = key_checker
        self.llm = llm or (LLM(model=self.settings.matcher.model)
                           if os.environ.get("ANTHROPIC_API_KEY") else None)
        self._rebuild()

    def _rebuild(self) -> None:
        if self.llm is None:
            self.agents = Agents()
        else:
            critic = Critic(self.llm, self.profile)
            self.agents = Agents(writer=Writer(self.llm, self.profile), critic=critic,
                                 tailor=Tailor(self.llm, critic, self.profile))

    def set_profile(self, profile: Profile) -> None:
        self.profile = profile
        self._rebuild()

    def set_settings(self, settings: Settings) -> None:
        model_changed = settings.matcher.model != self.settings.matcher.model
        self.settings = settings
        if model_changed and self.llm is not None and self._fixed_llm is None:
            self.llm = LLM(model=settings.matcher.model)
        self._rebuild()

    def key_changed(self) -> None:
        """Call after ANTHROPIC_API_KEY was set or removed in os.environ."""
        if self._fixed_llm is not None:
            return
        self.llm = LLM(model=self.settings.matcher.model) if os.environ.get("ANTHROPIC_API_KEY") else None
        self._rebuild()


async def desktop_running(state: AppState) -> bool:
    if state.settings.automation.backend != "docker":
        return True  # the local backend opens its own browser per run
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            return (await client.get(f"{state.settings.automation.cdp_url}/json/version")).is_success
    except httpx.HTTPError:
        return False


def create_app(
    profile_path: Path,
    settings_path: Path,
    llm: LLM | None = None,
    env_path: Path | None = None,
    key_checker: Callable[[str], None] | None = None,
    scheduler: bool = True,
) -> FastAPI:
    """`llm`, `env_path` and `key_checker` are injectable for tests."""
    from applypilot.ops_api import OpsState, register_ops_routes
    from applypilot.profile_api import register_profile_routes
    from applypilot.settings_api import register_settings_routes

    ensure_config_files(profile_path, settings_path)
    state = AppState(profile_path, settings_path, env_path or REPO / ".env", llm, key_checker)
    ops = OpsState(state)
    runs: dict[str, Run] = {}
    tasks: set[asyncio.Task] = set()

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(ops.schedule_loop()) if scheduler else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="ApplyPilot", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_methods=["*"], allow_headers=["*"],
    )
    for name, directory in (("screenshots", state.settings.automation.screenshots_dir),
                            ("resumes", state.settings.automation.resumes_dir)):
        directory.mkdir(parents=True, exist_ok=True)
        app.mount(f"/{name}", StaticFiles(directory=directory), name=name)

    def active() -> Run | None:
        return next((r for r in runs.values() if r.outcome is None), None)

    @app.get("/api/config")
    def config() -> dict:
        a = state.settings.automation
        return {"backend": a.backend, "novnc_url": a.novnc_url, "mode": state.settings.applicant.mode,
                "drafting": state.agents.writer is not None}

    @app.get("/api/status")
    async def status() -> dict:
        """Setup checklist for the first-run guide. Booleans and counts only: never a key."""
        s = state.settings
        counts = state.store.counts()
        return {
            "backend": s.automation.backend,
            "desktop_running": await desktop_running(state),
            "api_key_set": state.llm is not None,
            "example_profile": state.profile.person.email.endswith("@example.com"),
            "facts": len(state.profile.facts),
            "boards": len(s.sources.greenhouse) + len(s.sources.lever) + len(s.sources.ashby),
            "jobs": sum(n for st, n in counts.items() if st != ApplicationStatus.FILTERED_OUT),
            "mode": s.applicant.mode,
        }

    @app.post("/api/runs")
    async def start_run(body: StartRun) -> dict:
        row = state.store.get(body.job_id)
        if row is None:
            raise HTTPException(404, "job not found")
        if row.status not in STARTABLE:
            raise HTTPException(409, f"this job is already {row.status.replace('_', ' ')}")
        if (current := active()) is not None:
            raise HTTPException(409, f"run {current.id} is still in progress; one browser, one run")
        run = Run(job_id=body.job_id)
        runs[run.id] = run
        task = asyncio.create_task(run_application(
            run, store=state.store, settings=state.settings, profile=state.profile, agents=state.agents))
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
    register_settings_routes(app, state)
    register_ops_routes(app, state, ops)
    return app


def _get(runs: dict[str, Run], run_id: str) -> Run:
    if run_id not in runs:
        raise HTTPException(404, "run not found")
    return runs[run_id]
