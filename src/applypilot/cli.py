"""Command line interface: `applypilot --help`."""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from applypilot.config import load_profile, load_settings
from applypilot.llm import LLM
from applypilot.matcher import Matcher
from applypilot.models import ApplicationStatus
from applypilot.pipeline import run_sweep
from applypilot.state import InvalidTransition
from applypilot.store import JobRow, Store

app = typer.Typer(help="ApplyPilot: a multi-agent job search assistant.", no_args_is_help=True)
console = Console()

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
ProfileOpt = Annotated[Path, typer.Option("--profile", "-p", help="Fact bank YAML.")]
SettingsOpt = Annotated[Path, typer.Option("--settings", "-s", help="Settings YAML.")]
DEFAULT_PROFILE = Path("profile.yaml")
DEFAULT_SETTINGS = Path("settings.yaml")


def _load_dotenv() -> None:
    """Read .env from the current folder, then from the repo root. Real env vars win."""
    for path in (Path(".env"), Path(__file__).resolve().parents[2] / ".env"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() and not key.lstrip().startswith("#") and value.strip():
                os.environ.setdefault(key.strip(), value.strip().strip('"'))


def _store(settings_path: Path) -> Store:
    return Store(load_settings(settings_path).database)


def _resolve(store: Store, ref: str) -> JobRow:
    matches = store.find(ref)
    if not matches:
        raise typer.BadParameter(f"no job matches '{ref}'")
    if len(matches) > 1:
        raise typer.BadParameter(f"'{ref}' matches {len(matches)} jobs; use more of the ID")
    return matches[0]


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    _load_dotenv()
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    # The SDK warns on every run that an env API key outranks other credential sources.
    # That's the intended setup here, so keep it out of normal output.
    if not verbose:
        logging.getLogger("anthropic").setLevel(logging.ERROR)


@app.command()
def init(force: Annotated[bool, typer.Option(help="Overwrite existing files.")] = False) -> None:
    """Create profile.yaml and settings.yaml from the examples."""
    for name, dest in (("profile", DEFAULT_PROFILE), ("settings", DEFAULT_SETTINGS)):
        if dest.exists() and not force:
            console.print(f"[yellow]{dest} exists, leaving it alone[/]")
            continue
        shutil.copyfile(EXAMPLES / f"{name}.example.yaml", dest)
        console.print(f"[green]created {dest}[/]")
    console.print("Next: put your real facts in profile.yaml, then run `applypilot validate`.")


@app.command()
def validate(profile: ProfileOpt = DEFAULT_PROFILE, settings: SettingsOpt = DEFAULT_SETTINGS) -> None:
    """Check that the profile and settings files are valid."""
    p = load_profile(profile)
    s = load_settings(settings)
    boards = sum(len(getattr(s.sources, n)) for n in ("greenhouse", "lever", "ashby"))
    console.print(f"[green]OK[/] {len(p.facts)} facts, {boards} job boards, mode={s.applicant.mode}")


@app.command()
def sweep(
    profile: ProfileOpt = DEFAULT_PROFILE,
    settings: SettingsOpt = DEFAULT_SETTINGS,
    no_score: Annotated[bool, typer.Option("--no-score", help="Fetch and filter only.")] = False,
) -> None:
    """Run one sweep: fetch new postings, filter them, score the survivors."""
    p, s = load_profile(profile), load_settings(settings)
    store = Store(s.database)
    matcher = None if no_score else Matcher(LLM(model=s.matcher.model), p)
    with console.status("Sweeping") as status:
        stats = run_sweep(store, s, p, matcher, on_progress=status.update)
    console.print(
        f"fetched [bold]{stats.fetched}[/] | new [bold]{stats.new}[/] | "
        f"filtered out {stats.filtered_out} | scored {stats.scored} | "
        f"[green]ready {stats.ready}[/] | below threshold {stats.below_threshold} | "
        f"failed {stats.score_failures} | deferred {stats.deferred}"
    )
    for board, err in stats.source_errors.items():
        console.print(f"[red]source error[/] {board}: {err}")


@app.command()
def watch(
    profile: ProfileOpt = DEFAULT_PROFILE,
    settings: SettingsOpt = DEFAULT_SETTINGS,
    every: Annotated[float | None, typer.Option(help="Hours between sweeps.")] = None,
) -> None:
    """Sweep on a schedule until stopped (Ctrl+C)."""
    hours = every or load_settings(settings).schedule.sweep_every_hours
    while True:
        sweep(profile=profile, settings=settings, no_score=False)
        console.print(f"next sweep in {hours:g}h")
        time.sleep(hours * 3600)


@app.command()
def jobs(
    settings: SettingsOpt = DEFAULT_SETTINGS,
    status: Annotated[list[ApplicationStatus] | None, typer.Option("--status")] = None,
    min_score: Annotated[int | None, typer.Option("--min-score")] = None,
    limit: int = 30,
) -> None:
    """List tracked jobs, best scores first."""
    store = _store(settings)
    table = Table(show_lines=False)
    table.add_column("ID", no_wrap=True)
    for col in ("Score", "Status", "Company", "Title", "Location"):
        table.add_column(col, overflow="fold")
    for r in store.list(status, min_score, limit):
        table.add_row(
            r.id, str(r.score) if r.score is not None else "-", r.status, r.company, r.title,
            r.location,
        )
    console.print(table)
    console.print(" ".join(f"{k}={v}" for k, v in sorted(store.counts().items())))


@app.command()
def show(ref: str, settings: SettingsOpt = DEFAULT_SETTINGS) -> None:
    """Show one job with its fit assessment and history."""
    store = _store(settings)
    r = _resolve(store, ref)
    console.rule(f"{r.title} at {r.company}")
    console.print(f"{r.url}\nstatus: {r.status}   location: {r.location or '-'}")
    if a := r.assessment:
        console.print(f"\n[bold]score {a.score} ({a.verdict})[/]  {a.summary}")
        marks = {"met": "[green]met[/]", "partial": "[yellow]partial[/]", "missing": "[red]missing[/]"}
        for req in a.requirements:
            cites = f" [dim]{', '.join(req.fact_ids)}[/]" if req.fact_ids else ""
            console.print(f"  {marks[req.status]:>20}  {req.requirement}{cites}\n{'':>12}{req.note}")
        for d in a.dealbreakers:
            console.print(f"  [red]dealbreaker[/] {d}")
    console.print()
    _print_events(store, r.id)


@app.command("log")
def log_cmd(
    ref: Annotated[str | None, typer.Argument()] = None,
    settings: SettingsOpt = DEFAULT_SETTINGS,
    limit: int = 40,
) -> None:
    """Show the application log, for one job or everything."""
    store = _store(settings)
    _print_events(store, _resolve(store, ref).id if ref else None, limit)


@app.command()
def mark(
    ref: str,
    status: ApplicationStatus,
    note: Annotated[str, typer.Option("--note", "-n")] = "",
    settings: SettingsOpt = DEFAULT_SETTINGS,
) -> None:
    """Record a status change by hand, e.g. an interview invite or a rejection email."""
    store = _store(settings)
    r = _resolve(store, ref)
    try:
        store.transition(r.id, status, actor="user", note=note)
    except InvalidTransition as exc:
        raise typer.BadParameter(str(exc)) from exc
    console.print(f"[green]{r.id}[/]: {r.status} -> {status}")


@app.command()
def tailor(
    ref: str,
    profile: ProfileOpt = DEFAULT_PROFILE,
    settings: SettingsOpt = DEFAULT_SETTINGS,
) -> None:
    """Build a one-page resume PDF tailored to one job (needs the desktop or a local Chromium)."""
    import asyncio

    from applypilot.tailor import Tailor, build_resume_file
    from applypilot.writer import Critic

    p, s = load_profile(profile), load_settings(settings)
    row = _resolve(Store(s.database), ref)
    llm = LLM(model=s.matcher.model)
    with console.status("Tailoring"):
        resume = Tailor(llm, Critic(llm, p), p).tailor(row.posting)
        path, dropped = asyncio.run(build_resume_file(resume, s.automation, s.automation.resumes_dir))

    console.rule(f"{row.title} at {row.company}")
    if resume.summary:
        console.print(f"[bold]Summary[/] {resume.summary}\n")
    for b in resume.all_bullets:
        mark = "[green]verified[/]" if b.verified else "[yellow]fact's own wording[/]"
        console.print(f"- {b.text}  [dim]{b.fact_id}[/] {mark}")
    console.print(f"\nskills: {', '.join(resume.skills)}")
    if dropped:
        console.print(f"[dim]{dropped} bullets dropped to fit one page[/]")
    for issue in resume.issues:
        console.print(f"[yellow]review:[/] {issue}")
    console.print(f"\n[green]saved[/] {path}  (sources: {path.with_name('sources.json')})")


@app.command("eval")
def eval_cmd(
    profile: Annotated[Path, typer.Option("--profile", "-p")] = EXAMPLES / "profile.example.yaml",
    settings: SettingsOpt = DEFAULT_SETTINGS,
) -> None:
    """Score the Writer + Verifier on the eval cases (live model calls; costs money)."""
    from applypilot.evals import run_evals
    from applypilot.writer import Critic, Writer

    p = load_profile(profile)
    model = load_settings(settings).matcher.model if settings.exists() else "claude-opus-5-5"
    llm = LLM(model=model)
    repo = Path(__file__).resolve().parents[2]
    with console.status("Running evals"):
        card, out = run_evals(repo / "evals" / "cases", p, Writer(llm, p), Critic(llm, p),
                              repo / "evals" / "results")

    table = Table(title="Writer + Verifier eval")
    for col in ("Case", "Question", "Expect", "Outcome", "Verdict", "Rounds"):
        table.add_column(col, overflow="fold")
    colors = {"correct": "green", "overclaimed": "red", "missed": "yellow"}
    for r in card.results:
        table.add_row(r.case, r.label, r.expect, r.outcome,
                      f"[{colors[r.verdict]}]{r.verdict}[/]", str(r.rounds))
    console.print(table)
    s = card.summary()
    console.print(
        f"correct {s['correct']:.0%} | overclaimed {s['overclaimed']} | missed {s['missed']} | "
        f"first-pass {s['first_pass']:.0%} | lint hits {s['lint_hits']}\nfull results: {out}"
    )


desktop_app = typer.Typer(help="The agent's sandboxed desktop (Docker).", no_args_is_help=True)
app.add_typer(desktop_app, name="desktop")
REPO = Path(__file__).resolve().parents[2]


def _compose(*args: str) -> None:
    import subprocess

    raise typer.Exit(subprocess.call(["docker", "compose", "-f", str(REPO / "docker-compose.yml"), *args]))


@desktop_app.command("up")
def desktop_up() -> None:
    """Build (first time) and start the desktop container."""
    _compose("up", "-d", "--build")


@desktop_app.command("down")
def desktop_down() -> None:
    """Stop the desktop container. Your browser profile is kept."""
    _compose("down")


@app.command()
def serve(
    profile: ProfileOpt = DEFAULT_PROFILE,
    settings: SettingsOpt = DEFAULT_SETTINGS,
    port: int = 8765,
) -> None:
    """Start the API the live dashboard talks to."""
    import uvicorn

    from applypilot.server import create_app

    console.print(f"API on http://127.0.0.1:{port}  (dashboard: `cd web && npm run dev`)")
    uvicorn.run(create_app(profile, settings), host="127.0.0.1", port=port, log_level="warning")


def _print_events(store: Store, job_id: str | None, limit: int = 40) -> None:
    table = Table(title="Application log")
    for col in ("When (UTC)", "Job", "Change", "By", "Note"):
        table.add_column(col, overflow="fold")
    for e in reversed(store.events(job_id, limit)):
        table.add_row(e.at, e.job_id, f"{e.from_status or '-'} -> {e.to_status}", e.actor, e.note)
    console.print(table)


if __name__ == "__main__":
    app()
