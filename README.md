# ApplyPilot

A multi-agent job search assistant. It sweeps company job boards on a schedule,
scores every opening against a **fact bank** of what you've actually done,
writes tailored materials that can only say things that are true, and fills in
applications in a real browser you can watch and take over.

> **Status:** milestones 1 and 2 done (sweep, grounded scoring, Writer +
> Verifier with evals) and most of milestone 3 (the live computer). See the
> [roadmap](#roadmap).

## Why this one is different

Most auto-apply tools spray generic applications. ApplyPilot is built around
three rules:

1. **Nothing un-sourced gets said.** Your experience lives in `profile.yaml` as
   small, checkable facts with IDs. Every claim an agent makes has to cite fact
   IDs, and plain code (not another prompt) rejects anything that cites a fact
   that doesn't exist or makes a claim with no citation.
2. **Deterministic where possible, LLM where needed.** Job boards are read
   through their official JSON APIs, not scraped. Location, title, salary and
   freshness filters are plain code with a logged reason. The model only does
   what needs judgment.
3. **Every step is on the record.** Each job moves through an explicit state
   machine, and every transition is written to an append-only application log
   with who made it and why.

## Quick start

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/<you>/applypilot && cd applypilot
uv sync
uv run applypilot init          # creates profile.yaml and settings.yaml
# edit profile.yaml with your real facts, settings.yaml with the boards to watch
cp .env.example .env            # add ANTHROPIC_API_KEY
uv run applypilot validate
uv run applypilot sweep
uv run applypilot jobs --min-score 70
uv run applypilot show <job-id>
```

`profile.yaml`, `settings.yaml`, `.env` and `data/` are git-ignored, so your
personal data never ends up in the repo.

## Commands

| Command | What it does |
|---|---|
| `init` | Copy the example profile and settings into place |
| `validate` | Check both files against their schemas |
| `sweep` | Fetch new postings, filter them, score the survivors |
| `watch --every 6` | Sweep on a timer |
| `jobs` | List tracked jobs, best scores first (`--status`, `--min-score`) |
| `show <id>` | Fit assessment with cited evidence per requirement, plus history |
| `log [<id>]` | The application log, for one job or everything |
| `mark <id> <status>` | Record an interview invite, rejection or offer by hand |
| `tailor <id>` | Build a one-page resume PDF for one job, from your facts |
| `eval` | Score the Writer + Verifier on the eval cases (live model calls) |
| `desktop up` / `desktop down` | Start or stop the agent's sandboxed desktop (Docker) |
| `serve` | Start the API the live dashboard talks to |

## Watch it apply

The agent works in its own computer: a small Linux desktop in Docker running
Chromium. The dashboard shows that screen live, next to a timeline of every step
the agent takes and why.

```bash
uv run applypilot desktop up     # first run builds the image
uv run applypilot serve          # API on :8765
cd web && npm install && npm run dev   # dashboard on http://localhost:3100
```

- **Who's driving** is always shown at the top. **Pause** stops the agent before
  its next action; **Take over** hands you the mouse and keyboard in the live
  screen; **Hand back** lets the agent continue from wherever you left the page.
- The agent fills contact fields straight from your profile, then the Writer
  drafts the open questions (see below). Each drafted sentence appears in the
  timeline with the facts it cites. Anything it can't answer honestly is listed
  for you by name.
- **Each application gets a clean browser.** Its own tabs, cookies and form
  drafts, all closed when the run ends; leftover tabs are cleared at the start
  of the next. Nothing from one application can show up in another. Sign in
  inside the live screen when a site needs it.
- It submits on its own only when every condition holds: `auto` mode, fit score
  at or above `auto_submit_min_score`, a site policy that allows it, no empty
  required fields, every draft verified, and under the daily cap. Otherwise it
  stops at the submit button and lists which conditions failed. You choose
  **Submit for me** (it re-checks first) or submit yourself and press
  **I submitted it**.

No Docker? Set `automation.backend: local` to drive a visible Chromium window on
your own machine instead (`uv run playwright install chromium` first).

## How answers stay honest

The Writer drafts every open question on a form in one pass, from three kinds
of source, and every sentence must cite at least one:

| Source | Example | Used for |
|---|---|---|
| `fact:<id>` | `fact:exp-acme-latency` | Anything about your experience |
| `answer:<key>` | `answer:willing_to_relocate` | Logistics, from `answers:` in your profile |
| `posting:"<quote>"` | `posting:"handles 3 million requests a day"` | Anything about the company or role |

Then the Verifier checks each draft in two layers:

1. **Plain code** (`writer/checks.py`): every source resolves, posting quotes
   appear verbatim, every number in a sentence appears in a cited source, no
   stock phrases ("passionate", "leverage", "excited to"…), no em dashes or
   exclamation marks, within the field's length limit.
2. **An LLM critic** reads each sentence beside the text of its sources and
   rejects claims that inflate them ("led" from "built") or say nothing specific.

Failing drafts get one rewrite with the exact problems listed; anything still
failing is left for you. Consent, legal, salary (unless you've set it),
demographic and upload questions are always left for you.

### Tailored resumes

When a form asks for a resume, the Tailor builds a one-page PDF for that
posting. The model only chooses which facts to feature and rewords each into a
bullet; your name, contact details, employers, titles, dates and education come
straight from `profile.yaml` in code, and every job you list appears. Skills can
only be ones your facts list. Each bullet goes through the same Verifier as form
answers, plus a no-first-person rule; a bullet that still fails uses your
fact's own wording. If the page overflows, the least relevant bullets are
dropped until it fits. The file is named `Your_Name_Resume.pdf`, saved with a
`sources.json` recording which fact each bullet came from, and linked from the
run's timeline.

### Evals

`uv run applypilot eval` runs the Writer and Verifier on synthetic postings in
`evals/cases/`, where each question is marked with the honest outcome for the
example profile (`answer` or `skip`). The scorecard counts **overclaims**
(answering what it shouldn't), misses, first-pass rate and lint hits. Latest
run: 18/18 correct, 0 overclaims, 91% verified on the first draft, 0 lint hits.

### Sites that don't allow automation

Every page the agent opens is checked against a site policy before it acts:

| Policy | Sites | What happens |
|---|---|---|
| Allowed | Greenhouse, Lever, Ashby forms | The agent fills the form |
| Caution | Workday, any site with no policy on file | Warning shown; the agent fills but never submits |
| Prohibited | LinkedIn, Indeed, Glassdoor | Warning with a link to the site's terms; the agent doesn't load the page and hands the browser to you |

The check runs inside the browser wrapper every action goes through, including
after redirects, so no agent can skip it. If you decide to accept the risk on a
prohibited site, list it under `automation.accept_tos_risk_for`. You'll still
be warned every time and still submit by hand. Policy summaries are our reading
of each site's terms, not legal advice.

## How it works

```
 Scout ──► Hard filters ──► Matcher ──► Tailor / Writer ──► Verifier ──► Applicant ──► Tracker
 (APIs)    (plain code)     (LLM +       (LLM, cites        (code +       (browser,     (log, inbox,
                             grounding    fact IDs)          LLM critic)   live view)    follow-ups)
                             check)
```

Job lifecycle:

```
discovered ─► filtered_out
     │
     ▼
  scored ─► below_threshold ─┐
     │                       │ (manual override)
     ▼                       ▼
  drafting ─► awaiting_approval ─► submitting ─► submitted ─► interviewing ─► offer
     │                                  │              │              │
     └──────────────► (auto mode) ──────┘              └──► rejected ◄┘
                                                (withdrawn is reachable from any non-final state)
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design in detail.

## Job sources

| Source | Endpoint | Notes |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{token}/jobs` | Public, official |
| Lever | `api.lever.co/v0/postings/{token}` | Public, official; includes salary ranges |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{token}` | Public, official; includes compensation |

LinkedIn and Indeed aren't supported. Their terms prohibit automated access.

## Submission modes

Set in `settings.yaml` under `applicant.mode`:

- `auto`: submit without asking when the fit score is at least
  `auto_submit_min_score` **and** the Verifier passes every draft. Anything
  else waits for your approval.
- `approve`: always stop before the final Submit click.
- `draft`: only prepare materials.

`daily_submit_cap` applies in every mode. CAPTCHAs and logins are always
handed to you; the agent doesn't try to get around them.

## Roadmap

- [x] **M1: Sweep and match.** Greenhouse/Lever/Ashby sources, hard filters,
      grounded LLM fit scoring, SQLite state machine and application log, CLI.
- [x] **M2: Write.** Writer (screening answers, cover letters), Verifier
      (citations, verbatim quotes, number grounding, lint, LLM critic, one
      rewrite), standard answers in the profile, eval suite, and the Tailor
      (one-page resume PDF per job, uploaded during applications).
- [ ] **M3: Apply, live.**
  - [x] Docker desktop with noVNC, Next.js dashboard with live screen, step
        timeline, pause / take over / hand back
  - [x] Site automation policy with warnings and handoff
  - [x] Label-based form filler for Greenhouse, Lever and Ashby; stops at submit
  - [x] Answering screening questions with verified drafts; auto-submit gate
  - [x] One isolated browser context per application
  - [ ] Computer-use fallback for forms the filler can't read
- [ ] **M4: Track.** Scheduler service, inbox sync to detect replies and
      rejections, follow-up reminders, weekly digest.

## Development

```bash
uv run pytest
uv run ruff check .
```

Tests never call a live API. Job boards are mocked with `respx` and the model
is replaced with a stub client.

## License

MIT
