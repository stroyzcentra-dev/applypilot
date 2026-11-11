"""Things you'd otherwise do from a terminal, as dashboard actions.

  GET  /api/jobs                 job list (?show=active|all)
  GET  /api/job?id=              one job: fit assessment, application log, allowed status changes
  POST /api/job/status           record an outcome: applied, interviewing, rejected, offer, withdrawn
  POST /api/job/resume           tailor a resume PDF for the job
  POST /api/sweeps               find jobs now
  GET  /api/sweeps/current       progress of the current or last search, and when the next one runs
  GET  /api/desktop              is the agent's computer running
  POST /api/desktop/start|stop   start or stop it (starts Docker Desktop too, if it can)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from applypilot.matcher import Matcher
from applypilot.models import ApplicationStatus as S
from applypilot.pipeline import run_sweep
from applypilot.state import InvalidTransition, allowed
from applypilot.store import Store
from applypilot.tailor import build_resume_file

if TYPE_CHECKING:
    from applypilot.server import AppState

log = logging.getLogger(__name__)
REPO = Path(__file__).resolve().parents[2]

# Outcomes you record by hand, in the order the dashboard offers them.
MANUAL_STATUSES = (S.SUBMITTED, S.INTERVIEWING, S.OFFER, S.REJECTED, S.WITHDRAWN)


def _now() -> datetime:
    return datetime.now(UTC)


class OpsState:
    """Long-running work the dashboard starts: searches and the desktop container."""

    def __init__(self, state: AppState):
        self.state = state
        self.sweep: dict = {"running": False, "trigger": None, "progress": "", "started_at": None,
                            "finished_at": None, "stats": None, "error": None}
        last = state.store.last_sweep()
        if last:
            self.sweep.update(finished_at=last["finished_at"], started_at=last["started_at"],
                              stats=last["stats"])
        self.desktop = {"busy": False, "message": ""}

    # ---- searches ---------------------------------------------------------------

    def next_scheduled(self) -> str | None:
        sched = self.state.settings.schedule
        if not sched.enabled:
            return None
        last = self.sweep["finished_at"]
        if last is None:
            return _now().isoformat(timespec="seconds")
        nxt = datetime.fromisoformat(last) + timedelta(hours=sched.sweep_every_hours)
        return max(nxt, _now()).isoformat(timespec="seconds")

    def start_sweep(self, trigger: Literal["you", "schedule"]) -> bool:
        if self.sweep["running"]:
            return False
        self.sweep.update(running=True, trigger=trigger, progress="Starting", error=None,
                          started_at=_now().isoformat(timespec="seconds"), stats=None)
        asyncio.get_running_loop().create_task(self._sweep())
        return True

    async def _sweep(self) -> None:
        st = self.state
        settings, profile, llm = st.settings, st.profile, st.llm

        def work():
            # Its own connection: SQLite handles the locking between this thread and the API.
            store = Store(settings.database)
            try:
                matcher = Matcher(llm, profile) if llm else None
                return run_sweep(store, settings, profile, matcher,
                                 on_progress=lambda m: self.sweep.update(progress=m))
            finally:
                store.close()

        try:
            stats = await asyncio.to_thread(work)
            self.sweep.update(stats=asdict(stats), progress="Done")
        except Exception as exc:
            log.exception("sweep failed")
            self.sweep.update(error=f"{type(exc).__name__}: {exc}", progress="Failed")
        finally:
            self.sweep.update(running=False, finished_at=_now().isoformat(timespec="seconds"))

    async def schedule_loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                nxt = self.next_scheduled()
                if nxt and datetime.fromisoformat(nxt) <= _now() and not self.sweep["running"]:
                    self.start_sweep("schedule")
            await asyncio.sleep(30)

    # ---- desktop ------------------------------------------------------------------------

    async def _docker(self, *args: str, timeout: float = 900) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except TimeoutError:
            proc.kill()
            return 1, "timed out"
        return proc.returncode or 0, out.decode(errors="ignore")

    async def _docker_ready(self) -> bool:
        code, _ = await self._docker("info", timeout=20)
        return code == 0

    def _launch_docker_desktop(self) -> bool:
        if sys.platform != "win32":
            return False
        for base in (os.environ.get("LOCALAPPDATA", ""), os.environ.get("PROGRAMFILES", "")):
            for rel in (r"Programs\DockerDesktop\Docker Desktop.exe", r"Docker\Docker\Docker Desktop.exe"):
                exe = Path(base) / rel
                if base and exe.exists():
                    subprocess.Popen([str(exe)], close_fds=True)
                    return True
        return False

    async def desktop_action(self, action: Literal["start", "stop"]) -> None:
        compose = ["compose", "-f", str(REPO / "docker-compose.yml")]
        self.desktop.update(busy=True)
        try:
            if shutil.which("docker") is None:
                self.desktop["message"] = ("Docker isn't installed. Install Docker Desktop, or switch the "
                                           "browser to “This computer” in Settings.")
                return
            if not await self._docker_ready():
                if action == "stop":
                    self.desktop["message"] = "Docker isn't running, so the desktop is already stopped."
                    return
                if not self._launch_docker_desktop():
                    self.desktop["message"] = "Docker isn't running. Open Docker Desktop, then try again."
                    return
                self.desktop["message"] = "Starting Docker Desktop…"
                for _ in range(60):
                    await asyncio.sleep(3)
                    if await self._docker_ready():
                        break
                else:
                    self.desktop["message"] = "Docker Desktop didn't start in time. Open it, then try again."
                    return
            starting = "Starting the agent's computer (the first time builds it, a few minutes)…"
            self.desktop["message"] = starting if action == "start" else "Stopping…"
            code, out = await self._docker(*compose, *(["up", "-d"] if action == "start" else ["down"]))
            if code != 0:
                self.desktop["message"] = "Docker reported an error: " + out.strip().splitlines()[-1][:300]
                return
            if action == "start":
                for _ in range(20):  # Chromium takes a few seconds after the container starts
                    from applypilot.server import desktop_running

                    if await desktop_running(self.state):
                        break
                    await asyncio.sleep(1.5)
            self.desktop["message"] = "Running." if action == "start" else "Stopped."
        finally:
            self.desktop["busy"] = False


class StatusChange(BaseModel):
    id: str
    status: S
    note: str = ""


class JobRef(BaseModel):
    id: str


def register_ops_routes(app: FastAPI, state: AppState, ops: OpsState) -> None:
    from applypilot.server import desktop_running, job_json

    def get_row(job_id: str):
        row = state.store.get(job_id)
        if row is None:
            raise HTTPException(404, "job not found")
        return row

    @app.get("/api/jobs")
    def jobs(show: Literal["active", "all"] = "active", limit: int = 200) -> list[dict]:
        statuses = None if show == "all" else [s for s in S if s != S.FILTERED_OUT]
        return [job_json(r, state.settings) for r in state.store.list(statuses, None, limit)]

    @app.get("/api/job")
    def job(id: str) -> dict:
        row = get_row(id)
        return {
            **job_json(row, state.settings),
            "description": row.posting.description,
            "salary": [row.posting.salary_min_usd, row.posting.salary_max_usd],
            "assessment": row.assessment.model_dump() if row.assessment else None,
            "events": [e.__dict__ for e in state.store.events(row.id, limit=200)][::-1],
            "can_mark": [s for s in MANUAL_STATUSES if s in allowed(row.status)],
        }

    @app.post("/api/job/status")
    def set_status(body: StatusChange) -> dict:
        row = get_row(body.id)
        if body.status not in MANUAL_STATUSES:
            raise HTTPException(422, "That status is set by the agent, not by hand.")
        note = body.note or ("applied on your own" if body.status == S.SUBMITTED else "")
        try:
            state.store.transition(row.id, body.status, actor="user", note=note)
        except InvalidTransition as exc:
            raise HTTPException(409, str(exc)) from exc
        return job(row.id)

    @app.post("/api/job/resume")
    async def resume(body: JobRef) -> dict:
        row = get_row(body.id)
        tailor = state.agents.tailor
        if tailor is None:
            raise HTTPException(503, "Add your Anthropic API key in Settings to tailor resumes.")
        if not await desktop_running(state):
            raise HTTPException(503, "Start the agent's computer first; it prints the PDF.")
        resume = await asyncio.to_thread(tailor.tailor, row.posting)
        path, dropped = await build_resume_file(resume, state.settings.automation,
                                                state.settings.automation.resumes_dir)
        rel = path.relative_to(state.settings.automation.resumes_dir).as_posix()
        return {"file": rel, "bullets": len(resume.all_bullets), "dropped": dropped,
                "unverified": [b.fact_id for b in resume.all_bullets if not b.verified]}

    @app.post("/api/sweeps")
    async def start_sweep() -> dict:  # async: the search is started on the server loop
        if not ops.start_sweep("you"):
            raise HTTPException(409, "A search is already running.")
        return current_sweep()

    @app.get("/api/sweeps/current")
    def current_sweep() -> dict:
        return {**ops.sweep, "next_scheduled": ops.next_scheduled(),
                "scoring": state.llm is not None}

    @app.get("/api/desktop")
    async def desktop() -> dict:
        return {"running": await desktop_running(state), "backend": state.settings.automation.backend,
                **ops.desktop}

    @app.post("/api/desktop/{action}")
    async def desktop_action(action: Literal["start", "stop"]) -> dict:
        if state.settings.automation.backend != "docker":
            raise HTTPException(409, "The browser is set to “This computer”, so there's no desktop to start.")
        if ops.desktop["busy"]:
            raise HTTPException(409, "Already working on it.")
        asyncio.get_running_loop().create_task(ops.desktop_action(action))
        return {"running": await desktop_running(state), **ops.desktop, "busy": True}
