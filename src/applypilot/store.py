"""SQLite persistence: jobs, the application log (events), and sweep runs."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from applypilot.models import ApplicationStatus, FitAssessment, JobPosting
from applypilot.state import check_transition

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    dedupe_key    TEXT NOT NULL UNIQUE,
    source        TEXT NOT NULL,
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT NOT NULL,
    url           TEXT NOT NULL,
    posting_json  TEXT NOT NULL,
    status        TEXT NOT NULL,
    score         INTEGER,
    assessment    TEXT,
    first_seen    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL REFERENCES jobs(id),
    at          TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS events_job ON events(job_id);

CREATE TABLE IF NOT EXISTS sweeps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    stats       TEXT NOT NULL DEFAULT '{}'
);
"""

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def dedupe_key(job: JobPosting) -> str:
    """Same company + title + location is the same opening, whichever board listed it."""
    norm = lambda s: _NON_ALNUM.sub(" ", s.lower()).strip()  # noqa: E731
    return "|".join((norm(job.company), norm(job.title), norm(job.location)))


@dataclass
class JobRow:
    id: str
    company: str
    title: str
    location: str
    url: str
    status: ApplicationStatus
    score: int | None
    first_seen: str
    updated_at: str
    posting: JobPosting
    assessment: FitAssessment | None


@dataclass
class Event:
    job_id: str
    at: str
    from_status: str | None
    to_status: str
    actor: str
    note: str


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self.conn:
            yield self.conn

    # ---- jobs ------------------------------------------------------------

    def add_if_new(self, job: JobPosting) -> bool:
        """Insert a newly discovered job. Returns False if it (or a duplicate) exists."""
        now = _now()
        with self.tx() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO jobs
                   (id, dedupe_key, source, company, title, location, url, posting_json,
                    status, first_seen, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job.id, dedupe_key(job), job.source, job.company, job.title, job.location,
                    job.url, job.model_dump_json(), ApplicationStatus.DISCOVERED, now, now,
                ),
            )
            if cur.rowcount == 0:
                return False
            self._log(c, job.id, None, ApplicationStatus.DISCOVERED, "scout", f"found on {job.source}")
            return True

    def get(self, job_id: str) -> JobRow | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def find(self, prefix: str) -> list[JobRow]:
        """Look up by full ID or any unambiguous prefix/suffix, for friendlier CLI use."""
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE id = ? OR id LIKE ? OR id LIKE ?",
            (prefix, f"{prefix}%", f"%:{prefix}"),
        ).fetchall()
        return [self._row(r) for r in rows]

    def list(
        self,
        statuses: list[ApplicationStatus] | None = None,
        min_score: int | None = None,
        limit: int = 50,
    ) -> list[JobRow]:
        sql, args = "SELECT * FROM jobs WHERE 1=1", []
        if statuses:
            sql += f" AND status IN ({','.join('?' * len(statuses))})"
            args += [str(s) for s in statuses]
        if min_score is not None:
            sql += " AND score >= ?"
            args.append(min_score)
        sql += " ORDER BY score IS NULL, score DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    def transition(
        self,
        job_id: str,
        to: ApplicationStatus,
        *,
        actor: str,
        note: str = "",
        assessment: FitAssessment | None = None,
    ) -> None:
        with self.tx() as c:
            row = c.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            src = ApplicationStatus(row["status"])
            check_transition(src, to)
            if assessment is not None:
                c.execute(
                    "UPDATE jobs SET status=?, score=?, assessment=?, updated_at=? WHERE id=?",
                    (to, assessment.score, assessment.model_dump_json(), _now(), job_id),
                )
            else:
                c.execute(
                    "UPDATE jobs SET status=?, updated_at=? WHERE id=?", (to, _now(), job_id)
                )
            self._log(c, job_id, src, to, actor, note)

    def counts(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ---- application log -------------------------------------------------

    def events(self, job_id: str | None = None, limit: int = 100) -> list[Event]:
        sql, args = "SELECT * FROM events", []
        if job_id:
            sql += " WHERE job_id = ?"
            args.append(job_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        return [
            Event(r["job_id"], r["at"], r["from_status"], r["to_status"], r["actor"], r["note"])
            for r in self.conn.execute(sql, args).fetchall()
        ]

    def submitted_today(self) -> int:
        """Submissions logged since midnight UTC, for the daily cap."""
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        row = self.conn.execute(
            "SELECT COUNT(*) n FROM events WHERE to_status = ? AND at >= ?",
            (ApplicationStatus.SUBMITTED, midnight.isoformat(timespec="seconds")),
        ).fetchone()
        return row["n"]

    # ---- sweeps ----------------------------------------------------------

    def start_sweep(self) -> int:
        with self.tx() as c:
            return c.execute("INSERT INTO sweeps (started_at) VALUES (?)", (_now(),)).lastrowid

    def finish_sweep(self, sweep_id: int, stats: dict) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE sweeps SET finished_at=?, stats=? WHERE id=?",
                (_now(), json.dumps(stats), sweep_id),
            )

    # ---- internals -------------------------------------------------------

    @staticmethod
    def _log(c: sqlite3.Connection, job_id, src, dst, actor, note) -> None:
        c.execute(
            "INSERT INTO events (job_id, at, from_status, to_status, actor, note)"
            " VALUES (?,?,?,?,?,?)",
            (job_id, _now(), str(src) if src else None, str(dst), actor, note),
        )

    @staticmethod
    def _row(r: sqlite3.Row) -> JobRow:
        return JobRow(
            id=r["id"],
            company=r["company"],
            title=r["title"],
            location=r["location"],
            url=r["url"],
            status=ApplicationStatus(r["status"]),
            score=r["score"],
            first_seen=r["first_seen"],
            updated_at=r["updated_at"],
            posting=JobPosting.model_validate_json(r["posting_json"]),
            assessment=FitAssessment.model_validate_json(r["assessment"]) if r["assessment"] else None,
        )
