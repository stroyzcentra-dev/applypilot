import httpx
import pytest
import respx

from applypilot.config import Sources
from applypilot.matcher import ScoredJob
from applypilot.models import ApplicationStatus as S
from applypilot.models import FitAssessment, JobPosting
from applypilot.pipeline import run_sweep
from applypilot.state import InvalidTransition
from tests.fixtures import ASHBY, GREENHOUSE, LEVER


def job(i="1", **kw) -> JobPosting:
    base = dict(source="greenhouse", external_id=i, company="acme",
                title="Software Engineer", url="https://x", location="Raleigh, NC")
    return JobPosting(**{**base, **kw})


def test_dedupe_across_sources(store):
    assert store.add_if_new(job())
    assert not store.add_if_new(job())  # same posting again
    assert not store.add_if_new(job(source="lever", external_id="zz"))  # same opening elsewhere
    assert store.add_if_new(job("2", title="Backend Engineer"))


def test_transitions_are_enforced_and_logged(store):
    store.add_if_new(job())
    jid = job().id
    with pytest.raises(InvalidTransition):
        store.transition(jid, S.SUBMITTED, actor="test")
    store.transition(jid, S.WITHDRAWN, actor="user", note="not interested")
    with pytest.raises(InvalidTransition):
        store.transition(jid, S.DISCOVERED, actor="user")
    history = [(e.from_status, e.to_status) for e in reversed(store.events(jid))]
    assert history == [(None, "discovered"), ("discovered", "withdrawn")]


class StubMatcher:
    def __init__(self, scores: dict[str, int]):
        self.scores = scores

    def score(self, posting: JobPosting) -> ScoredJob:
        s = self.scores.get(posting.title, 50)
        return ScoredJob(FitAssessment(score=s, verdict="possible", summary="stub",
                                       requirements=[]))


@respx.mock
def test_sweep_end_to_end(store, settings, profile):
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE))
    respx.get("https://api.lever.co/v0/postings/globex").mock(
        return_value=httpx.Response(200, json=LEVER))
    respx.get("https://api.ashbyhq.com/posting-api/job-board/initech").mock(
        return_value=httpx.Response(200, json=ASHBY))
    settings.sources = Sources(greenhouse=["acme"], lever=["globex"], ashby=["initech"])

    matcher = StubMatcher({"Backend Engineer, Payments": 90, "Software Engineer": 75})
    stats = run_sweep(store, settings, profile, matcher, workers=2)

    assert stats.fetched == 5 and stats.new == 5
    assert stats.filtered_out == 2  # Engineering Manager; hybrid NYC role
    assert stats.scored == 3 and stats.ready == 2 and stats.below_threshold == 1

    counts = store.counts()
    assert counts == {"filtered_out": 2, "scored": 2, "below_threshold": 1}

    # Second sweep finds nothing new and rescores nothing.
    again = run_sweep(store, settings, profile, matcher)
    assert again.new == 0 and again.scored == 0
