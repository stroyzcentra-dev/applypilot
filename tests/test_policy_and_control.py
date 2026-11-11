import asyncio

import pytest

from applypilot.live.control import Mode, RunCancelled, RunControl
from applypilot.live.events import EventBus
from applypilot.policy import Action, Level, decide, lookup


@pytest.mark.parametrize(
    ("url", "level", "action"),
    [
        ("https://job-boards.greenhouse.io/acme/jobs/1", Level.ALLOWED, Action.AUTOMATE),
        ("https://jobs.lever.co/globex/abc/apply", Level.ALLOWED, Action.AUTOMATE),
        ("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/1", Level.CAUTION,
         Action.AUTOMATE_NO_AUTOSUBMIT),
        ("https://careers.some-startup.com/apply", Level.CAUTION, Action.AUTOMATE_NO_AUTOSUBMIT),
        ("https://www.linkedin.com/jobs/view/123", Level.PROHIBITED, Action.HANDOFF),
        ("https://www.indeed.com/viewjob?jk=1", Level.PROHIBITED, Action.HANDOFF),
    ],
)
def test_policy_levels(url, level, action):
    d = decide(url)
    assert d.policy.level == level and d.action == action
    assert (d.warning is None) == (level == Level.ALLOWED)


def test_lookalike_domain_is_not_trusted():
    assert lookup("https://greenhouse.io.evil.example/apply").level == Level.CAUTION
    assert lookup("https://notlinkedin.com/x").level == Level.CAUTION


def test_accepting_risk_still_warns_and_never_autosubmits():
    d = decide("https://www.linkedin.com/jobs/view/1", accept_tos_risk_for=["linkedin.com"])
    assert d.may_automate and not d.may_autosubmit
    assert "accept that risk" in d.warning and "user-agreement" in d.warning


async def test_take_over_blocks_agent_until_handed_back():
    c = RunControl()
    c.take_over()
    assert c.mode == Mode.USER
    waiter = asyncio.create_task(c.checkpoint())
    await asyncio.sleep(0.01)
    assert not waiter.done()
    c.hand_back()
    await asyncio.wait_for(waiter, 1)
    assert c.mode == Mode.AGENT


async def test_cancel_wakes_a_paused_agent():
    c = RunControl()
    c.pause()
    waiter = asyncio.create_task(c.checkpoint())
    await asyncio.sleep(0.01)
    c.cancel()
    with pytest.raises(RunCancelled):
        await asyncio.wait_for(waiter, 1)


async def test_decision_round_trip():
    c = RunControl()
    assert not c.decide("agent_submit")  # nothing to decide yet
    pending = asyncio.create_task(c.await_decision())
    await asyncio.sleep(0.01)
    assert c.mode == Mode.WAITING_APPROVAL
    assert c.decide("user_submitted")
    assert await pending == "user_submitted"


async def test_late_subscriber_gets_history_then_live():
    bus = EventBus()
    bus.emit("action", "one")
    seen = []

    async def consume():
        async for s in bus.subscribe():
            seen.append(s.title)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    bus.emit("action", "two")
    bus.close()
    await asyncio.wait_for(task, 1)
    assert seen == ["one", "two"]
