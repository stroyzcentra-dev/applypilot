from types import SimpleNamespace

import pytest

from applypilot.config import AutomationSettings
from applypilot.llm import LLM
from applypilot.models import Fact, JobPosting
from applypilot.tailor import Tailor, to_html
from applypilot.tailor.models import BulletDraft, ResumeDraft
from applypilot.tailor.render import page_count, render_pdf
from applypilot.tailor.tailor import EN_DASH
from applypilot.writer.models import AnswerReview, Critique, SentenceReview
from applypilot.writer.writer import Critic

JOB = JobPosting(
    source="lever",
    external_id="1",
    company="globex",
    title="Backend Engineer",
    url="https://x",
    description="Python APIs at scale with Postgres and Redis.",
)


class ScriptedLLM(LLM):
    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls = []
        self.model = "fake"

    def structured(self, *, system, user, schema, **kw):
        self.calls.append(SimpleNamespace(schema=schema, user=user))
        return self.script[schema].pop(0)


def all_ok(n):
    return Critique(
        reviews=[
            AnswerReview(
                key="resume",
                answers_the_question=True,
                sentences=[SentenceReview(index=i, supported=True, specific=True) for i in range(n)],
            )
        ]
    )


GOOD = ResumeDraft(
    summary="Backend engineer who built and owns a FastAPI freight pricing service.",
    summary_fact_ids=["exp-acme-api"],
    bullets=[
        BulletDraft(
            fact_id="exp-acme-latency",
            text="Cut p95 latency of the quote service from 900ms to 180ms with Redis caching",
        ),
        BulletDraft(
            fact_id="exp-acme-api",
            text="Built and own a FastAPI pricing service handling about 40k requests per day",
        ),
        BulletDraft(
            fact_id="proj-applypilot",
            text="Built ApplyPilot, an open-source multi-agent job search assistant",
        ),
    ],
    skills=["Python", "FastAPI", "redis", "Kubernetes"],
)


def make(profile, drafts, critiques):
    llm = ScriptedLLM({ResumeDraft: drafts, Critique: critiques})
    return Tailor(llm, Critic(llm, profile), profile), llm


def test_layout_comes_from_the_profile_and_wording_is_verified(profile):
    tailor, llm = make(profile, [GOOD], [all_ok(3)])
    r = tailor.tailor(JOB)

    assert r.name == "Jordan Example" and "jordan@example.com" in r.contact
    [role] = r.roles
    assert (role.org, role.title) == ("Acme Logistics", "Software Engineer")
    assert role.dates == f"Jun 2024 {EN_DASH} Present"
    assert [b.fact_id for b in role.bullets] == ["exp-acme-latency", "exp-acme-api"]  # model's order
    assert all(b.verified for b in r.all_bullets)
    assert r.projects[0].link == "github.com/jordan-example/applypilot"
    assert r.education == ["NC State University, B.S. in Computer Science, 2024"]
    # Skills: only ones some fact lists, in the profile's casing. Kubernetes isn't in any fact.
    assert r.skills == ["Python", "FastAPI", "Redis"]
    assert r.summary and r.summary_verified
    assert [c.schema for c in llm.calls] == [ResumeDraft, Critique]


def test_inflated_bullet_falls_back_to_the_facts_own_words(profile):
    inflated = GOOD.model_copy(deep=True)
    inflated.bullets[0].text = "Cut p95 latency by 80% from 900ms to 180ms"  # 80 is in no source
    inflated.bullets.append(BulletDraft(fact_id="exp-invented", text="Led a team of 8"))
    tailor, llm = make(profile, [inflated, inflated], [all_ok(2), all_ok(2)])

    r = tailor.tailor(JOB)

    latency = next(b for b in r.all_bullets if b.fact_id == "exp-acme-latency")
    assert not latency.verified and latency.text.startswith("Cut p95 latency of the quote service")
    assert "80" in " ".join(latency.issues)
    assert "exp-invented" not in [b.fact_id for b in r.all_bullets]
    retry = [c for c in llm.calls if c.schema is ResumeDraft][1]
    assert "failed review" in retry.user and "exp-acme-latency" in retry.user


def test_critic_overstatement_is_caught(profile):
    tailor, _ = make(
        profile,
        [GOOD, GOOD],
        [
            Critique(
                reviews=[
                    AnswerReview(
                        key="resume",
                        answers_the_question=True,
                        sentences=[
                            SentenceReview(index=0, supported=True, specific=True),
                            SentenceReview(index=1, supported=False, specific=True, note="'own' overstates"),
                            SentenceReview(index=2, supported=True, specific=True),
                        ],
                    )
                ]
            ),
            all_ok(3),
        ],
    )
    r = tailor.tailor(JOB)
    assert all(b.verified for b in r.all_bullets)  # the retry passed


def test_every_job_appears_even_without_bullets(profile):
    profile.facts.append(
        Fact(
            id="exp-old",
            kind="experience",
            org="Initech",
            role="Intern",
            start="2023-05",
            end="2023-08",
            text="Wrote internal reporting scripts in Python.",
        )
    )
    tailor, _ = make(profile, [GOOD], [all_ok(3)])
    r = tailor.tailor(JOB)
    assert [x.org for x in r.roles] == ["Acme Logistics", "Initech"]
    assert r.roles[1].bullets == []


def test_html_escapes_and_has_standard_sections(profile):
    tailor, _ = make(profile, [GOOD], [all_ok(3)])
    r = tailor.tailor(JOB)
    r.roles[0].bullets[0].text = "Shipped <script>x</script> & more"
    html = to_html(r)
    assert "<script>x" not in html and "&lt;script&gt;" in html
    for section in ("Summary", "Experience", "Projects", "Education", "Skills"):
        assert f"<h2>{section}</h2>" in html


async def test_pdf_is_trimmed_to_one_page(profile, tmp_path):
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            await (await pw.chromium.connect_over_cdp("http://localhost:9223", timeout=3000)).close()
        except Exception:
            pytest.skip("desktop container isn't running")

    tailor, _ = make(profile, [GOOD], [all_ok(3)])
    r = tailor.tailor(JOB)
    long = r.roles[0].bullets[0]
    r.roles[0].bullets += [long.model_copy(update={"text": f"{long.text} ({i})"}) for i in range(60)]

    pdf, dropped = await render_pdf(r, AutomationSettings(screenshots_dir=tmp_path))
    assert pdf.startswith(b"%PDF") and page_count(pdf) == 1 and dropped > 0


def test_first_person_bullet_is_rejected(profile):
    first_person = GOOD.model_copy(deep=True)
    first_person.bullets[
        1
    ].text = "Own a FastAPI pricing service handling about 40k requests per day, which I built"
    tailor, _ = make(profile, [first_person, first_person], [all_ok(2), all_ok(2)])
    r = tailor.tailor(JOB)
    api = next(b for b in r.all_bullets if b.fact_id == "exp-acme-api")
    assert not api.verified and "first person" in " ".join(api.issues)


def test_resume_file_is_named_for_the_person(tmp_path):
    from applypilot.tailor.render import resume_path

    p = resume_path(tmp_path, "ashby:ramp:939892e5", "Jordan Example")
    assert p.name == "Jordan_Example_Resume.pdf" and p.parent.name == "ashby_ramp_939892e5"
