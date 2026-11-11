"""Form filling against a real browser.

Uses the desktop container's Chromium when it's running, otherwise a local
Playwright Chromium if installed; skips if neither is available.
"""

import pytest
from playwright.async_api import async_playwright

from applypilot.applicant.forms import (
    fill_standard_fields,
    find_submit,
    required_still_empty,
    scan,
)
from applypilot.config import AutomationSettings
from applypilot.live.browser import AgentPage
from applypilot.live.control import RunControl
from applypilot.live.events import EventBus

# Shaped like a Greenhouse hosted form: labels with for=, asterisks for required,
# a hidden-behind-a-button file input, and a custom screening question.
FORM = """
<form>
  <label for="first_name">First Name *</label><input id="first_name" required>
  <label for="last_name">Last Name *</label><input id="last_name" required>
  <label for="email">Email *</label><input id="email" type="email" required>
  <label for="phone">Phone</label><input id="phone">
  <div class="field"><label>Resume/CV *</label><input type="file" style="display:none" required></div>
  <label for="li">LinkedIn Profile</label><input id="li">
  <label for="q1">Why do you want to work here? *</label><textarea id="q1" required></textarea>
  <label for="auth">Are you authorized to work in the US? *</label>
  <select id="auth" required><option value=""></option><option>Yes</option></select>
  <label for="company">Current company name</label><input id="company">
  <button type="submit">Submit Application</button>
</form>
"""


# Shaped like Ashby, from a real Ramp form: question labels point for= at a question
# id that isn't a control, required asterisks are drawn with CSS, multi-choice
# questions are a fieldset of individually labeled checkboxes, and an unlabeled
# "autofill from resume" upload sits at the top.
ASHBY_FORM = """
<style>.req::after { content: "*"; color: red; }</style>
<div><input type="file"></div>
<div><label for="_systemfield_name">Legal Name</label><div><input id="_systemfield_name" required></div></div>
<div><label class="req" for="q-payroll">Where do you plan on working from?</label>
  <div><div><input placeholder="Start typing..."></div></div></div>
<fieldset><label class="req" for="q-loc">What is your location preference?</label>
  <div><span><input type="checkbox" id="loc-0"></span><label for="loc-0">NYC</label></div>
  <div><span><input type="checkbox" id="loc-1"></span><label for="loc-1">Remote - US</label></div>
</fieldset>
"""


@pytest.fixture
async def page():
    async with async_playwright() as pw:
        browser = None
        try:
            browser = await pw.chromium.connect_over_cdp("http://localhost:9223", timeout=3000)
            ctx = browser.contexts[0]
        except Exception:
            try:
                browser = await pw.chromium.launch()
                ctx = await browser.new_context()
            except Exception:
                pytest.skip("no browser available (run `applypilot desktop up`)")
        p = await ctx.new_page()
        yield p
        await p.close()


async def test_fills_only_known_fields_and_reports_the_rest(page, profile, tmp_path):
    await page.set_content(FORM)
    bus = EventBus()
    ap = AgentPage(page, bus=bus, control=RunControl(), run_id="t",
                   settings=AutomationSettings(screenshots_dir=tmp_path))

    result = await fill_standard_fields(ap, profile.person)

    assert result.filled == ["First Name", "Last Name", "Email", "Phone", "LinkedIn Profile"]
    assert await page.input_value("#first_name") == "Jordan"
    assert await page.input_value("#last_name") == "Example"
    assert await page.input_value("#li") == "https://www.linkedin.com/in/jordan-example"
    # Not guessed: screening answers, the dropdown, a resume we don't have, other names.
    assert set(result.needs_input) == {
        "Resume/CV", "Why do you want to work here?", "Are you authorized to work in the US?",
    }
    assert await page.input_value("#company") == ""
    # Every fill is on the timeline with where the value came from.
    actions = [s for s in bus.history if s.kind == "action"]
    assert len(actions) == 5 and all("in your profile" in s.detail for s in actions)
    # about:blank has no policy on file, so the user was warned before anything was filled.
    assert bus.history[0].kind == "warning"

    assert set(await required_still_empty(ap)) == set(result.needs_input)
    assert await (await find_submit(ap)).inner_text() == "Submit Application"


async def test_ashby_questions_groups_and_css_required(page, profile, tmp_path):
    await page.set_content(ASHBY_FORM)
    ap = AgentPage(page, bus=EventBus(), control=RunControl(), run_id="t",
                   settings=AutomationSettings(screenshots_dir=tmp_path))

    fields = {f.clean_label: f for f in await scan(ap)}
    assert fields["What is your location preference?"].options == 2  # one question, not two
    assert fields["What is your location preference?"].required  # asterisk from CSS
    assert fields["Where do you plan on working from?"].required
    assert "NYC" not in fields and "Legal Name" in fields
    assert "" in fields  # the unlabeled upload didn't borrow a neighbor's label

    result = await fill_standard_fields(ap, profile.person)
    assert result.filled == ["Legal Name"]
    assert await page.input_value("#_systemfield_name") == "Jordan Example"
    assert set(result.needs_input) == {
        "Where do you plan on working from?", "What is your location preference?",
    }
