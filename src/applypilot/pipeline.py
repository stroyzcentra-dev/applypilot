"""The sweep: Scout -> hard filters -> Matcher, recorded in the application log.

Later milestones add Tailor/Writer/Verifier (drafting) and the Applicant
(browser submission) as further stages on the same state machine.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field

import anthropic

from applypilot.config import Settings
from applypilot.llm import LLMBadOutput, LLMRefusal
from applypilot.matcher import Matcher, rejection_reason
from applypilot.models import ApplicationStatus as S
from applypilot.models import Profile
from applypilot.sources import scout
from applypilot.store import JobRow, Store

log = logging.getLogger(__name__)


@dataclass
class SweepStats:
    fetched: int = 0
    new: int = 0
    filtered_out: int = 0
    scored: int = 0
    ready: int = 0
    below_threshold: int = 0
    score_failures: int = 0
    deferred: int = 0
    source_errors: dict[str, str] = field(default_factory=dict)


def run_sweep(
    store: Store,
    settings: Settings,
    profile: Profile,
    matcher: Matcher | None,
    *,
    on_progress: Callable[[str], None] = lambda _msg: None,
    workers: int = 4,
) -> SweepStats:
    stats = SweepStats()
    sweep_id = store.start_sweep()

    # 1. Scout
    on_progress("Scouting job boards")
    found = asyncio.run(scout(settings.sources))
    stats.fetched = len(found.postings)
    stats.source_errors = found.errors
    for job in found.postings:
        if store.add_if_new(job):
            stats.new += 1

    # 2. Hard filters over everything still waiting (new now, or left from last sweep)
    on_progress("Applying hard filters")
    waiting: list[JobRow] = []
    for row in store.list([S.DISCOVERED], limit=100_000):
        if reason := rejection_reason(row.posting, profile.preferences):
            store.transition(row.id, S.FILTERED_OUT, actor="filters", note=reason)
            stats.filtered_out += 1
        else:
            waiting.append(row)

    # 3. Matcher (LLM), capped per sweep; the rest wait for the next one
    if matcher is None:
        stats.deferred = len(waiting)
    else:
        cap = settings.matcher.max_scored_per_sweep
        batch, stats.deferred = waiting[:cap], max(0, len(waiting) - cap)
        on_progress(f"Scoring {len(batch)} jobs")
        _score_batch(store, settings, matcher, batch, stats, workers)

    store.finish_sweep(sweep_id, asdict(stats))
    return stats


def _score_batch(store, settings, matcher, batch, stats, workers) -> None:
    threshold = settings.matcher.min_score_to_draft
    # Model calls run in threads; every DB write stays on this thread.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(matcher.score, row.posting): row for row in batch}
        for fut in as_completed(futures):
            row = futures[fut]
            try:
                result = fut.result()
            except (LLMRefusal, LLMBadOutput) as exc:
                store.transition(row.id, S.FAILED, actor="matcher", note=str(exc))
                stats.score_failures += 1
                continue
            except anthropic.APIError as exc:
                # Transient (rate limit, outage): leave it DISCOVERED so the next sweep retries.
                log.warning("scoring %s deferred: %s", row.id, exc)
                stats.deferred += 1
                continue

            a = result.assessment
            note = a.summary
            if result.grounding_issues:
                note += f" [grounding fixes: {len(result.grounding_issues)}]"
            store.transition(row.id, S.SCORED, actor="matcher", note=note, assessment=a)
            stats.scored += 1
            if a.score < threshold:
                store.transition(
                    row.id, S.BELOW_THRESHOLD, actor="matcher",
                    note=f"score {a.score} < {threshold}",
                )
                stats.below_threshold += 1
            else:
                stats.ready += 1
