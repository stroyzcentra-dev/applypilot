from datetime import UTC, datetime, timedelta

from applypilot.matcher.filters import rejection_reason
from applypilot.models import JobPosting


def job(**kw) -> JobPosting:
    base = dict(
        source="greenhouse", external_id="1", company="acme", title="Software Engineer",
        url="https://x", location="Raleigh, NC",
    )
    return JobPosting(**{**base, **kw})


def test_passes_matching_job(profile):
    assert rejection_reason(job(), profile.preferences) is None


def test_title_words_match_in_any_order(profile):
    assert rejection_reason(job(title="Senior Software Engineer, Backend"), profile.preferences) is None


def test_avoided_title_keyword(profile):
    reason = rejection_reason(job(title="Staff Software Engineer"), profile.preferences)
    assert reason and "staff" in reason


def test_avoided_keyword_is_whole_word(profile):
    # "intern" is avoided, but "Internal Tools" should not trip it.
    assert rejection_reason(job(title="Software Engineer, Internal Tools"), profile.preferences) is None


def test_unrelated_title(profile):
    assert "target title" in rejection_reason(job(title="Account Executive"), profile.preferences)


def test_location_and_remote(profile):
    assert "location" in rejection_reason(job(location="Austin, TX"), profile.preferences)
    assert rejection_reason(job(location="Austin, TX", remote=True), profile.preferences) is None


def test_salary_floor_only_rejects_stated_low_max(profile):
    assert "salary" in rejection_reason(job(salary_max_usd=70000), profile.preferences)
    assert rejection_reason(job(salary_max_usd=None), profile.preferences) is None


def test_stale_posting(profile):
    old = datetime.now(UTC) - timedelta(days=90)
    assert "days ago" in rejection_reason(job(posted_at=old), profile.preferences)
