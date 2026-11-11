# Architecture

## Goals

1. Do the full job of a diligent applicant: find openings, judge fit honestly,
   research the company, tailor materials, apply, track, follow up.
2. Never put a false or vague claim in front of a recruiter.
3. Be watchable and interruptible. The user can see what the agent is doing
   and take over at any point.

## Agents

| Agent | Input | Output | Kind |
|---|---|---|---|
| Scout | board tokens | `JobPosting[]` | Code (official JSON APIs) |
| Filters | posting + preferences | reject reason or pass | Code |
| Matcher | posting + fact bank | `FitAssessment` | LLM, then code verification |
| Researcher (M2) | company, posting | `CompanyBrief` with source URLs | LLM + web tools |
| Tailor | posting + facts | one-page resume PDF; bullets each restate one fact | LLM picks and rewords; code builds layout |
| Writer | posting + facts + standard answers | form answers and cover letters, every sentence cited | LLM |
| Verifier | any draft | pass, or a list of violations | Code + LLM critic |
| Applicant (M3) | approved package + apply URL | submission result, screenshots | Playwright; ATS mappers first, computer use as fallback |
| Tracker (M4) | inbox, calendar | status transitions | LLM classification + code |

Agents never pass free text to each other. Every boundary is a Pydantic model
(`src/applypilot/models.py`), and LLM calls use structured outputs so a
malformed response fails at the boundary.

## Grounding

The fact bank (`profile.yaml`) is the only source of truth about the user.
Each fact is one small claim with a stable ID:

```yaml
- id: exp-acme-latency
  kind: achievement
  text: Cut p95 latency of the quote service from 900ms to 180ms by adding a Redis cache ...
```

Agents must cite fact IDs for every claim. After each LLM call, code checks the
output (`matcher/scorer.py: verify`):

- cited IDs that don't exist are removed and recorded as grounding issues;
- a requirement marked `met` or `partial` with no remaining evidence is
  downgraded to `missing` and the score is reduced;
- explicit dealbreakers cap the score and force a `no` verdict.

The Writer and Verifier (`writer/`) extend this to prose. Each drafted
sentence cites `fact:`, `answer:` (standard answers in the profile) or
`posting:"<verbatim quote>"` sources. `checks.py` verifies in plain code that
sources resolve, quotes are verbatim, every number appears in a cited source,
and no stock phrases, em dashes or over-length text get through. Drafts that
pass go to an LLM critic that sees each sentence beside its source texts and
flags overstatement or filler. Failures get one rewrite with the exact issues;
what still fails goes to the user. `evals/` measures the whole loop against
expected answer/skip outcomes, with overclaims as the headline error.

Each application runs in its own browser context, created at run start and
closed at run end, so tabs, cookies and form state never cross between
applications.

## State and the application log

Each job is a row in `jobs` with a status. Allowed transitions are declared in
`state.py`; `Store.transition` refuses anything else. Every transition appends
a row to `events` (time, from, to, actor, note), which is the application log.
`sweeps` records stats for each run.

SQLite is enough for one user. Swapping to Postgres only touches `store.py`.

## Cost and safety controls

- Hard filters run before any model call.
- `matcher.max_scored_per_sweep` caps LLM calls per sweep; the rest are
  scored next time.
- The fact bank sits in a cached system prompt, so scoring many jobs against
  the same profile reuses it from cache.
- Transient API errors leave a job `discovered` for retry; refusals and bad
  outputs move it to `failed` with the reason logged.
- Submission requires the Verifier to pass, respects `daily_submit_cap`, and
  in `approve` mode always waits for the user.

## Live computer (M3)

```
 web/ (Next.js :3100)                 applypilot serve (FastAPI :8765)            desktop container
 ┌───────────────────────┐  REST     ┌──────────────────────────────┐   CDP     ┌──────────────────┐
 │ who's-driving banner  │ ───────►  │ Run: RunControl + EventBus   │ ───────►  │ Chromium          │
 │ live screen (iframe)  │ ◄─ SSE ── │ AgentPage (policy, steps,    │  :9223    │ Xvfb + fluxbox    │
 │ step timeline         │           │   screenshots)               │           │ x11vnc + noVNC    │
 └──────────┬────────────┘           └──────────────────────────────┘           └────────┬─────────┘
            └────────────────────────── noVNC websocket :6080 ───────────────────────────┘
```

- **Desktop** (`desktop/`): Debian with Chromium, a virtual display and noVNC.
  Ports are bound to localhost only. Chromium keeps its own sandbox (the
  container gets `SYS_ADMIN` for that rather than running `--no-sandbox`), and
  its profile lives in a volume so the user's sign-ins persist.
- **AgentPage** (`live/browser.py`) is the only way agents touch a page. Every
  action waits at the control checkpoint, re-checks site policy for the current
  URL, runs, screenshots, and adds a step with the agent's stated reason.
- **RunControl** (`live/control.py`) holds who is driving. Pause and take-over
  clear an event the agent awaits before its next action, so it never acts on
  a page the user is using. Submission is a separate decision future the user
  resolves from the dashboard.
- **EventBus** (`live/events.py`) replays history to late subscribers, so
  opening the dashboard mid-run still shows every step.

## Site policy

`policy.py` maps domains to allowed / caution / prohibited with a reason and a
link to the terms. `decide()` turns that into automate, automate without
auto-submit, or hand off. Prohibited destinations are checked *before*
navigation, so the agent never loads them on its own; the user can press
"Open page" to load the posting themselves. Users can accept the risk per
domain, which downgrades a handoff to a warning but never enables auto-submit.

## Form filling

`applicant/forms.py` reads each field the way a person does: by its label, or
for checkbox and radio groups, by the question above them (including required
markers drawn with CSS). It fills only fields that map to a profile fact and
reports every unanswered required question by name. Site-specific CSS
selectors aren't used, so one filler handles Greenhouse, Lever and Ashby.
