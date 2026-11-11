"""Load and validate the user's profile and settings files."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, Field

from applypilot.models import Profile

BoardToken = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=100)]


class Sources(BaseModel):
    """Company job boards to watch, by ATS. The token is the company's slug in its
    careers URL: boards.greenhouse.io/<token>, jobs.lever.co/<token>, jobs.ashbyhq.com/<token>."""

    greenhouse: list[BoardToken] = Field(default_factory=list)
    lever: list[BoardToken] = Field(default_factory=list)
    ashby: list[BoardToken] = Field(default_factory=list)


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
    # When on, the API searches for new jobs on its own every `sweep_every_hours`.
    enabled: bool = False
    sweep_every_hours: float = Field(default=6, ge=1, le=168)


class Settings(BaseModel):
    sources: Sources = Field(default_factory=Sources)
    matcher: MatcherSettings = Field(default_factory=MatcherSettings)
    applicant: ApplicantSettings = Field(default_factory=ApplicantSettings)
    schedule: ScheduleSettings = Field(default_factory=ScheduleSettings)
    automation: AutomationSettings = Field(default_factory=AutomationSettings)
    database: Path = Path("data/applypilot.db")


EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def ensure_config_files(profile_path: Path, settings_path: Path) -> list[Path]:
    """Create profile.yaml / settings.yaml from the examples if they're missing.

    Lets a fresh clone start straight into the dashboard. Returns what it created.
    """
    created = []
    for path, example in ((profile_path, "profile"), (settings_path, "settings")):
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(EXAMPLES / f"{example}.example.yaml", path)
            created.append(path)
    return created


def write_yaml(path: Path, data: dict, header: str) -> None:
    """Write YAML atomically, keeping the previous file as <name>.bak."""
    text = header + "\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.with_name(path.name + ".bak").write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


SETTINGS_HEADER = """# ApplyPilot settings. Edited from the dashboard's Settings page.
# The previous version is kept in settings.yaml.bak.
"""


def save_settings(path: Path, settings: Settings) -> None:
    write_yaml(path, settings.model_dump(mode="json"), SETTINGS_HEADER)


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"{path} not found. Run `applypilot init` to create it.")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def load_profile(path: Path) -> Profile:
    return Profile.model_validate(_read_yaml(path))


def load_settings(path: Path) -> Settings:
    return Settings.model_validate(_read_yaml(path))
