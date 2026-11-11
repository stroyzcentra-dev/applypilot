"""Load and validate the user's profile and settings files."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from applypilot.models import Profile


class Sources(BaseModel):
    greenhouse: list[str] = Field(default_factory=list)
    lever: list[str] = Field(default_factory=list)
    ashby: list[str] = Field(default_factory=list)


class MatcherSettings(BaseModel):
    model: str = "claude-opus-5-5"
    min_score_to_draft: int = Field(default=70, ge=0, le=100)
    max_scored_per_sweep: int = Field(default=40, ge=1)


class ApplicantSettings(BaseModel):
    mode: Literal["auto", "approve", "draft"] = "auto"
    auto_submit_min_score: int = Field(default=85, ge=0, le=100)
    daily_submit_cap: int = Field(default=10, ge=0)


class AutomationSettings(BaseModel):
    # docker: Chromium in a sandboxed desktop container you watch through noVNC.
    # local:  a visible Chromium window on this machine (no Docker needed).
    backend: Literal["docker", "local"] = "docker"
    cdp_url: str = "http://localhost:9223"
    novnc_url: str = "http://localhost:6080/vnc.html?autoconnect=1&resize=scale&view_only=0"
    # Domains whose terms forbid automation but where you accept the risk anyway.
    # You'll still get a warning every time, and you'll still submit by hand.
    accept_tos_risk_for: list[str] = Field(default_factory=list)
    screenshots_dir: Path = Path("data/screenshots")
    # Tailored resume PDFs, each with a JSON record of which fact every bullet came from.
    resumes_dir: Path = Path("data/resumes")


class ScheduleSettings(BaseModel):
    sweep_every_hours: float = Field(default=6, gt=0)


class Settings(BaseModel):
    sources: Sources = Field(default_factory=Sources)
    matcher: MatcherSettings = Field(default_factory=MatcherSettings)
    applicant: ApplicantSettings = Field(default_factory=ApplicantSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    database: Path = Path("data/applypilot.db")


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `applypilot init` to create it.")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_profile(path: Path) -> Profile:
    return Profile.model_validate(_read_yaml(path))


def load_settings(path: Path) -> Settings:
    return Settings.model_validate(_read_yaml(path))
