import io
import shutil
import zipfile
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

from applypilot.config import load_profile
from applypilot.llm import LLM
from applypilot.profile_tools import (
    ExtractedContact,
    ExtractedFact,
    ExtractedProfile,
    Refinement,
    SuggestedFact,
    extract_text,
    new_fact_id,
)
from applypilot.server import create_app
from tests.conftest import EXAMPLES

RESUME = """Sam Rivera
sam.rivera@example.org | (555) 010-2000 | Durham, NC | github.com/samrivera

Experience
Data Engineer, Brightpath Health   Mar 2022 - Present
- Built Airflow pipelines that load 2 TB of claims data nightly into Snowflake
- Reduced warehouse cost by 30% by partitioning the largest tables

Education
B.S. Statistics, UNC Chapel Hill, 2021
"""


class ScriptedLLM(LLM):
    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.model = "fake"

    def structured(self, *, system, user, schema, **kw):
        self.calls.append(SimpleNamespace(schema=schema, user=user))
        return self.script[schema].pop(0)


@pytest.fixture
def files(tmp_path):
    profile = tmp_path / "profile.yaml"
    shutil.copy(EXAMPLES / "profile.example.yaml", profile)
    settings = tmp_path / "settings.yaml"
    settings.write_text(
        yaml.safe_dump(
            {
                "database": str(tmp_path / "db.sqlite"),
                "automation": {
                    "screenshots_dir": str(tmp_path / "shots"),
                    "resumes_dir": str(tmp_path / "resumes"),
                },
            }
        )
    )
    return profile, settings


def client(files, llm=None) -> TestClient:
    return TestClient(create_app(*files, llm=llm))


# ---- read and save ------------------------------------------------------------------


def test_round_trip_save_keeps_a_backup(files):
    c = client(files)
    p = c.get("/api/profile").json()
    p["person"]["name"] = "Jordan Q. Example"
    p["facts"].append(
        {"id": "proj-new", "kind": "project", "text": "Wrote a CLI that renames photos by date."}
    )

    r = c.put("/api/profile", json=p)

    assert r.status_code == 200
    saved = load_profile(files[0])
    assert saved.person.name == "Jordan Q. Example" and "proj-new" in saved.fact_ids
    backup = files[0].with_name("profile.yaml.bak").read_text(encoding="utf-8")
    assert "Jordan Example" in backup and "proj-new" not in backup
    assert c.get("/api/profile").json()["person"]["name"] == "Jordan Q. Example"  # reloaded


def test_invalid_profile_is_rejected_and_file_untouched(files):
    c = client(files)
    before = files[0].read_text(encoding="utf-8")
    p = c.get("/api/profile").json()
    p["facts"].append(dict(p["facts"][0]))  # duplicate id
    p["facts"].append({"id": "Bad Id!", "kind": "project", "text": "short"})

    r = c.put("/api/profile", json=p)

    assert r.status_code == 422
    assert files[0].read_text(encoding="utf-8") == before


def test_ai_endpoints_explain_when_no_key(files):
    r = client(files).post(
        "/api/profile/refine", json={"id": "x-1", "kind": "project", "text": "Built a thing for people."}
    )
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


# ---- import -----------------------------------------------------------------------


def extraction(**overrides):
    facts = [
        ExtractedFact(
            kind="experience",
            text="Data Engineer at Brightpath Health.",
            source_quote="Data Engineer, Brightpath Health Mar 2022 - Present",
            org="Brightpath Health",
            role="Data Engineer",
            start="2022-03",
            end="present",
        ),
        ExtractedFact(
            kind="achievement",
            org="Brightpath Health",
            text="Built Airflow pipelines that load 2 TB of claims data nightly into Snowflake.",
            source_quote="Built Airflow pipelines that load 2 TB of claims data nightly into Snowflake",
            skills=["Airflow", "Snowflake"],
        ),
        # Inflated: the resume says 30%, not 45%.
        ExtractedFact(
            kind="achievement",
            org="Brightpath Health",
            text="Reduced warehouse cost by 45% by partitioning tables.",
            source_quote="Reduced warehouse cost by 30% by partitioning the largest tables",
        ),
        # Invented: not in the resume at all.
        ExtractedFact(
            kind="achievement",
            org="Brightpath Health",
            text="Led a team of 6 data engineers.",
            source_quote="Led a team of 6 data engineers",
        ),
    ]
    return ExtractedProfile(
        contact=ExtractedContact(
            name="Sam Rivera",
            email="sam.rivera@example.org",
            phone="(555) 010-2000",
            location="Durham, NC",
            links={"github": "https://github.com/samrivera", "linkedin": "https://linkedin.com/in/made-up"},
        ),
        facts=overrides.get("facts", facts),
        suggested_titles=["Data Engineer", "Analytics Engineer"],
    )


def test_import_checks_every_fact_against_the_resume(files):
    llm = ScriptedLLM({ExtractedProfile: [extraction()]})
    r = client(files, llm).post(
        "/api/profile/import", files={"file": ("resume.txt", RESUME.encode(), "text/plain")}
    )
    assert r.status_code == 200
    body = r.json()

    def verdict(prefix):
        f = next(f for f in body["facts"] if f["fact"]["text"].startswith(prefix))
        return f["verified"], " ".join(f["problems"])

    assert verdict("Data Engineer")[0]
    assert verdict("Built Airflow")[0]
    ok, why = verdict("Reduced warehouse")
    assert not ok and "45" in why
    ok, why = verdict("Led a team")
    assert not ok and "isn't in the resume" in why

    assert body["contact"]["links"] == {"github": "https://github.com/samrivera"}  # invented link dropped
    ids = [f["fact"]["id"] for f in body["facts"]]
    assert len(set(ids)) == len(ids) and ids[0].startswith("exp-")
    assert "<resume>" in llm.calls[0].user and "Brightpath" in llm.calls[0].user


def test_import_reads_docx(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        paras = "".join(f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>" for line in RESUME.splitlines())
        z.writestr("word/document.xml", f"<w:document><w:body>{paras}</w:body></w:document>")
    text = extract_text("resume.docx", buf.getvalue())
    assert "Brightpath Health" in text and "2 TB" in text


def test_import_rejects_unreadable_files(files):
    llm = ScriptedLLM({})
    c = client(files, llm)
    r = c.post("/api/profile/import", files={"file": ("photo.png", b"\x89PNG....", "image/png")})
    assert r.status_code == 422 and "PDF" in r.json()["detail"]
    r = c.post("/api/profile/import", files={"file": ("empty.txt", b"hi", "text/plain")})
    assert r.status_code == 422
    assert llm.calls == []  # never sent to the model


def test_new_fact_ids_are_unique_and_readable():
    taken = {"exp-brightpath-health"}
    assert new_fact_id("experience", "Brightpath Health", taken) == "exp-brightpath-health-2"
    assert new_fact_id("project", "A CLI for photos", taken) == "proj-cli-for-photos"


# ---- refine --------------------------------------------------------------------------


def test_refine_drops_suggestions_that_add_numbers(files):
    llm = ScriptedLLM(
        {
            Refinement: [
                Refinement(
                    verdict="improve",
                    why="It doesn't say what the service does at what scale.",
                    suggestions=[
                        SuggestedFact(
                            text="Built a FastAPI service that prices freight quotes.", kind="experience"
                        ),
                        SuggestedFact(
                            text="Built a FastAPI service serving 2 million users.", kind="experience"
                        ),
                    ],
                    questions=["How many requests per day does it handle?"],
                )
            ]
        }
    )
    r = client(files, llm).post(
        "/api/profile/refine",
        json={"id": "exp-x", "kind": "experience", "text": "Built a FastAPI service at work."},
    )
    body = r.json()
    assert [s["text"] for s in body["refinement"]["suggestions"]] == [
        "Built a FastAPI service that prices freight quotes."
    ]
    assert "2" in body["rejected"][0]
    assert body["refinement"]["questions"] == ["How many requests per day does it handle?"]


def test_skill_facts_merge_and_meta_facts_drop(files):
    from applypilot.profile_tools import verify_import

    quote = "Python, SQL, Airflow, dbt, Snowflake"
    facts = [ExtractedFact(kind="skill", text=s, source_quote=quote, skills=[s]) for s in ("Python", "SQL")]
    facts += [
        ExtractedFact(
            kind="skill", text="Airflow and dbt", source_quote=quote, skills=["Airflow", "dbt", "SQL"]
        ),
        ExtractedFact(kind="other", text="Lists SQL and Tableau among skills", source_quote=quote),
    ]
    r = verify_import(ExtractedProfile(contact=ExtractedContact(), facts=facts), RESUME + "\n" + quote, set())
    [skill] = r.facts
    assert skill.fact.text == "Skills: Python, SQL, Airflow, dbt." and skill.verified
    assert skill.fact.skills == ["Python", "SQL", "Airflow", "dbt"]


def test_import_links_get_a_scheme_so_the_profile_saves(files):
    from applypilot.profile_tools import verify_import

    r = verify_import(
        ExtractedProfile(contact=ExtractedContact(links={"GitHub": "github.com/samrivera"}), facts=[]),
        RESUME,
        set(),
    )
    assert r.contact.links == {"github": "https://github.com/samrivera"}
