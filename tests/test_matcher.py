from types import SimpleNamespace

import pytest

from applypilot.llm import LLM, LLMRefusal
from applypilot.matcher import Matcher, verify
from applypilot.matcher.scorer import build_system
from applypilot.models import FitAssessment, JobPosting, RequirementMatch


def assessment(**kw) -> FitAssessment:
    base = dict(
        score=88, verdict="strong", summary="Python API work matches.",
        requirements=[
            RequirementMatch(requirement="3+ years Python", status="met",
                             fact_ids=["exp-acme-api"], note="Owns a FastAPI service."),
        ],
    )
    return FitAssessment(**{**base, **kw})


def test_verify_keeps_grounded_assessment(profile):
    out = verify(assessment(), profile)
    assert out.grounding_issues == [] and out.assessment.score == 88


def test_verify_strips_invented_facts_and_downgrades(profile):
    a = assessment(requirements=[
        RequirementMatch(requirement="Kubernetes", status="met",
                         fact_ids=["exp-made-up"], note="Runs k8s clusters."),
    ])
    out = verify(a, profile)
    req = out.assessment.requirements[0]
    assert req.status == "missing" and req.fact_ids == []
    assert out.assessment.score == 83
    assert len(out.grounding_issues) == 2
    assert a.requirements[0].status == "met"  # original untouched


def test_dealbreaker_caps_score(profile):
    out = verify(assessment(dealbreakers=["Requires TS/SCI clearance"]), profile)
    assert out.assessment.score <= 20 and out.assessment.verdict == "no"


def test_system_prompt_contains_every_fact_id(profile):
    system = build_system(profile)
    for fid in profile.fact_ids:
        assert f"[{fid}]" in system


def test_user_prompt_states_today():
    from datetime import date

    from applypilot.matcher.scorer import build_user

    job = JobPosting(source="lever", external_id="1", company="g", title="SWE", url="https://x")
    assert "Today is 2026-11-10" in build_user(job, today=date(2026, 11, 10))


class FakeClient:
    """Stands in for anthropic.Anthropic; records the request, returns a canned response."""

    def __init__(self, response):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(parse=self._parse))
        self._response = response

    def _parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


def test_matcher_end_to_end_with_fake_model(profile):
    client = FakeClient(SimpleNamespace(stop_reason="end_turn", parsed_output=assessment()))
    m = Matcher(LLM(client=client), profile)
    job = JobPosting(source="lever", external_id="1", company="globex",
                     title="Software Engineer", url="https://x", description="Python APIs")
    out = m.score(job)
    assert out.assessment.score == 88
    call = client.calls[0]
    assert call["output_format"] is FitAssessment
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "Python APIs" in call["messages"][0]["content"]


def test_refusal_raises(profile):
    resp = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"),
                           parsed_output=None)
    llm = LLM(client=FakeClient(resp))
    with pytest.raises(LLMRefusal):
        llm.structured(system="s", user="u", schema=FitAssessment)
