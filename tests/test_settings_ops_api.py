import shutil
import time

import anthropic
import httpx
import pytest
import respx
import yaml
from fastapi.testclient import TestClient

from applypilot.config import load_settings
from applypilot.models import JobPosting
from applypilot.server import create_app
from applypilot.settings_api import parse_board, write_env_var
from applypilot.store import Store
from tests.conftest import EXAMPLES
from tests.fixtures import GREENHOUSE

GOOD_KEY = "sk-ant-api03-" + "x" * 40


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    profile, settings, env = tmp_path / "profile.yaml", tmp_path / "settings.yaml", tmp_path / ".env"
    shutil.copy(EXAMPLES / "profile.example.yaml", profile)
    settings.write_text(
        yaml.safe_dump(
            {
                "database": str(tmp_path / "db.sqlite"),
                "sources": {"greenhouse": ["acme"]},
                "automation": {
                    "screenshots_dir": str(tmp_path / "shots"),
                    "resumes_dir": str(tmp_path / "res"),
                },
            }
        )
    )
    env.write_text("# my notes\nOTHER_VAR=keep-me\n")
    return profile, settings, env


def make(paths, checker=lambda key: None) -> TestClient:
    profile, settings, env = paths
    return TestClient(create_app(profile, settings, env_path=env, key_checker=checker, scheduler=False))


# ---- settings ---------------------------------------------------------------------------


def test_settings_round_trip_and_paths_stay_put(paths):
    with make(paths) as c:
        body = c.get("/api/settings").json()
        assert body["api_key_set"] is False
        s = body["settings"]
        s["applicant"]["mode"] = "approve"
        s["sources"]["lever"] = ["globex"]
        s["schedule"]["enabled"] = True
        s["database"] = "C:/elsewhere.db"  # not editable from the dashboard

        r = c.put("/api/settings", json=s)

        assert r.status_code == 200
        saved = load_settings(paths[1])
        assert saved.applicant.mode == "approve" and saved.sources.lever == ["globex"]
        assert saved.schedule.enabled and str(saved.database).endswith("db.sqlite")
        assert paths[1].with_name("settings.yaml.bak").exists()
        assert c.get("/api/config").json()["mode"] == "approve"  # applied without a restart


def test_invalid_settings_are_rejected(paths):
    with make(paths) as c:
        s = c.get("/api/settings").json()["settings"]
        s["sources"]["greenhouse"] = ["not a token!"]
        s["applicant"]["auto_submit_min_score"] = 140
        r = c.put("/api/settings", json=s)
        assert r.status_code == 422
        fields = {".".join(map(str, e["loc"][1:])) for e in r.json()["detail"]}
        assert {"sources.greenhouse.0", "applicant.auto_submit_min_score"} <= fields


# ---- API key -----------------------------------------------------------------------------


def test_api_key_is_checked_saved_and_never_returned(paths):
    seen = []
    with make(paths, checker=seen.append) as c:
        r = c.put("/api/settings/api-key", json={"key": GOOD_KEY})
        assert r.status_code == 200 and r.json()["api_key_set"] is True
        assert seen == [GOOD_KEY]
        env = paths[2].read_text()
        assert f"ANTHROPIC_API_KEY={GOOD_KEY}" in env and "OTHER_VAR=keep-me" in env and "# my notes" in env
        for path in ("/api/settings", "/api/status", "/api/config"):
            assert GOOD_KEY not in c.get(path).text
        assert c.get("/api/status").json()["api_key_set"] is True

        r = c.delete("/api/settings/api-key")
        assert r.json()["api_key_set"] is False
        assert "ANTHROPIC_API_KEY" not in paths[2].read_text() and "OTHER_VAR=keep-me" in paths[2].read_text()


def test_bad_keys_are_not_saved(paths):
    def reject(key):
        raise anthropic.AuthenticationError(
            "invalid x-api-key",
            body=None,
            response=httpx.Response(401, request=httpx.Request("GET", "https://api.anthropic.com")),
        )

    with make(paths, checker=reject) as c:
        assert c.put("/api/settings/api-key", json={"key": "hello-this-is-not-a-key"}).status_code == 422
        r = c.put("/api/settings/api-key", json={"key": GOOD_KEY})
        assert r.status_code == 422 and "rejected" in r.json()["detail"]
    assert "ANTHROPIC_API_KEY" not in paths[2].read_text()


def test_write_env_var_replaces_duplicates(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nANTHROPIC_API_KEY=old\nANTHROPIC_API_KEY=older\n")
    write_env_var(env, "ANTHROPIC_API_KEY", "new")
    assert env.read_text() == "A=1\nANTHROPIC_API_KEY=new\n"


# ---- boards ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://boards.greenhouse.io/stripe/jobs/123", ("greenhouse", "stripe")),
        ("job-boards.greenhouse.io/embed/job_board?for=Airbnb", ("greenhouse", "airbnb")),
        ("https://jobs.lever.co/palantir", ("lever", "palantir")),
        ("jobs.ashbyhq.com/Ramp/abc-123/application", ("ashby", "ramp")),
        ("Linear", (None, "linear")),
        ("Scale AI", (None, "scaleai")),
    ],
)
def test_parse_board(text, expected):
    assert parse_board(text) == expected


@respx.mock
def test_check_board_finds_which_ats_a_name_is_on(paths):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE)
    )
    respx.get("https://api.lever.co/v0/postings/acme").mock(return_value=httpx.Response(404))
    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme").mock(return_value=httpx.Response(404))
    with make(paths) as c:
        r = c.post("/api/settings/check-board", json={"input": "Acme"}).json()
        assert (r["source"], r["token"], r["jobs"]) == ("greenhouse", "acme", 2)


# ---- jobs and searches --------------------------------------------------------------------


def seed_job(paths) -> str:
    db = load_settings(paths[1]).database
    store = Store(db)
    job = JobPosting(
        source="greenhouse",
        external_id="9",
        company="acme",
        title="Software Engineer",
        url="https://x",
        location="Remote",
        remote=True,
    )
    store.add_if_new(job)
    store.close()
    return job.id


def test_job_detail_and_recording_outcomes(paths):
    jid = seed_job(paths)
    with make(paths) as c:
        d = c.get("/api/job", params={"id": jid}).json()
        assert d["status"] == "discovered" and "submitted" in d["can_mark"]
        assert "offer" not in d["can_mark"]  # can't get an offer before applying

        d = c.post("/api/job/status", json={"id": jid, "status": "submitted"}).json()
        assert d["status"] == "submitted" and d["events"][-1]["note"] == "applied on your own"
        d = c.post(
            "/api/job/status", json={"id": jid, "status": "interviewing", "note": "phone screen"}
        ).json()
        assert d["status"] == "interviewing" and set(d["can_mark"]) == {"offer", "rejected", "withdrawn"}

        assert c.post("/api/job/status", json={"id": jid, "status": "drafting"}).status_code == 422
        assert c.get("/api/job", params={"id": "nope"}).status_code == 404


@respx.mock
def test_find_jobs_runs_in_the_background(paths):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE)
    )
    with make(paths) as c:
        assert c.post("/api/sweeps").status_code == 200
        for _ in range(100):
            s = c.get("/api/sweeps/current").json()
            if not s["running"]:
                break
            time.sleep(0.05)
        assert s["error"] is None and s["stats"]["fetched"] == 2 and s["stats"]["new"] == 2
        assert s["scoring"] is False  # no key: found and filtered, scoring deferred
        assert len(c.get("/api/jobs", params={"show": "all"}).json()) == 2
