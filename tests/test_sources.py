import httpx
import respx

from applypilot.config import Sources
from applypilot.sources import scout
from applypilot.sources._text import html_to_text
from tests.fixtures import ASHBY, GREENHOUSE, LEVER


@respx.mock
async def test_scout_parses_every_source_and_survives_a_bad_board():
    respx.get("https://boards-api.greenhouse.io/v1/boards/acme/jobs").mock(
        return_value=httpx.Response(200, json=GREENHOUSE)
    )
    respx.get("https://boards-api.greenhouse.io/v1/boards/gone/jobs").mock(
        return_value=httpx.Response(404)
    )
    respx.get("https://api.lever.co/v0/postings/globex").mock(
        return_value=httpx.Response(200, json=LEVER)
    )
    respx.get("https://api.ashbyhq.com/posting-api/job-board/initech").mock(
        return_value=httpx.Response(200, json=ASHBY)
    )

    result = await scout(Sources(greenhouse=["acme", "gone"], lever=["globex"], ashby=["initech"]))

    assert set(result.errors) == {"greenhouse:gone"}
    by_id = {p.id: p for p in result.postings}
    assert len(by_id) == 5  # unlisted Ashby job is skipped

    gh = by_id["greenhouse:acme:4012345"]
    assert gh.remote and gh.department == "Engineering"
    assert "- 3+ years Python" in gh.description and "&lt;" not in gh.description

    lv = by_id["lever:globex:a1b2c3d4-0000-0000-0000-000000000001"]
    assert (lv.salary_min_usd, lv.salary_max_usd) == (110000, 140000)
    assert not lv.remote and "Requirements" in lv.description and lv.posted_at

    ab = by_id["ashby:initech:f00d-1"]
    assert ab.remote and ab.salary_max_usd == 190000

    hybrid = by_id["ashby:initech:f00d-3"]
    assert not hybrid.remote
    assert hybrid.location == "New York, NY (HQ); San Francisco, CA"


def test_html_to_text_keeps_structure():
    text = html_to_text("<h2>About</h2><p>We ship.</p><ul><li>Python</li><li>Go</li></ul>")
    assert text.splitlines()[0] == "About"
    assert "- Python" in text and "- Go" in text
