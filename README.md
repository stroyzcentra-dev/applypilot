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

Needs [uv](https://docs.astral.sh/uv/), [Node.js](https://nodejs.org/) and,
for the watchable desktop, [Docker Desktop](https://www.docker.com/products/docker-desktop/).

```bash
git clone https://github.com/stroyzcentra-dev/applypilot
```

Then start it. On Windows, double-click **`start-applypilot.cmd`** in the project
folder. Anywhere else:

```bash
./start-applypilot.sh
```

That starts the API, the dashboard and the agent's desktop, and opens
**http://localhost:3100**. Everything after that happens in the dashboard:

| Page | What you do there |
|---|---|
| **Settings** | Add your Anthropic API key, the companies to watch, how picky to be, whether to search automatically, and how much the agent may submit on its own. Start or stop the agent's computer. |
| **Profile** | Import your resume, then review and refine your facts and standard answers. |
| **Jobs** | Find jobs, see each one's fit and why, start applications, make tailored resumes, record interviews and offers. |

The first time you open it, a setup guide checks off each step as you do it.
Your settings, profile, key and data stay on your computer in git-ignored
files (`settings.yaml`, `profile.yaml`, `.env`, `data/`).

## Command line

Everything is in the dashboard, but the same actions exist as commands for
scripting: `uv run applypilot --help`.

| Command | What it does |
|---|---|
| `start` | Start everything and open the dashboard (what the launcher runs) |
| `sweep` / `watch` | Find jobs once, or on a timer |
| `jobs`, `show <id>`, `log`, `mark <id> <status>` | List, inspect and update jobs |
| `tailor <id>` | Build a one-page resume PDF for one job |
| `eval` | Score the Writer + Verifier on the eval cases (live model calls) |
| `desktop up` / `down`, `serve`, `init`, `validate` | The pieces `start` runs for you |

## Watch it apply

The agent works in its own computer: a small Linux desktop in Docker running
Chromium. The dashboard shows that screen live, next to a timeline of every step
the agent takes and why.

### Your profile, in the dashboard

The **Profile** page edits everything in `profile.yaml` without touching YAML:
contact details, what you're looking for, answers to common questions, and
your facts.

- **Start from your resume.** Upload a PDF, Word or text resume and Claude
  proposes facts, one claim each. Code then checks every proposal against the
  resume's own text: its quoted line must be in the resume, and so must every
  number, company and year. Anything that fails is shown unticked with the
  reason. Nothing is added until you choose, and nothing is saved until you
  press Save.
- **Refine with AI** on any fact suggests sharper wording or a split into
  separate claims, and asks the questions that would make it stronger ("How
  many requests per day?"). It never fills in numbers; suggestions that add one
  are hidden.
- Saving validates the whole profile, keeps the previous version as
  `profile.yaml.bak`, and the next application uses the new facts.

The setup guide (**How it works** in the header) tracks the same checklist:
API key, profile, companies, the agent's computer, and jobs found.

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

Set on the Settings page, under Applying:

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

## Keeping your keys and data private

- Your API key lives only in `.env`, which is git-ignored (as is every
  `.env.*` except `.env.example`). The dashboard never receives it; the setup
  guide only reports whether one is set.
- `profile.yaml`, `settings.yaml`, `data/` (screenshots, resumes, the database)
  and eval outputs are git-ignored too.
- Every service listens on `127.0.0.1` only: API, dashboard, noVNC viewer and
  the browser's DevTools port.
- `tests/test_no_secrets.py` fails if any tracked file looks like it holds a
  credential, and CI runs it on every push. GitHub secret scanning and push
  protection are on for this repository.

## Development

```bash
uv run pytest
uv run ruff check .
```

Tests never call a live API. Job boards are mocked with `respx` and the model
is replaced with a stub client.

## License

MIT
