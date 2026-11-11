"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ValidationError, type BoardResult, type Desktop, type Settings, type SettingsView } from "@/lib/api";
import { NumberField, TagInput, Toggle } from "../profile/fields";

const SOURCE_LABEL = { greenhouse: "Greenhouse", lever: "Lever", ashby: "Ashby" } as const;
type Source = keyof typeof SOURCE_LABEL;

const MODELS = [
  { id: "claude-opus-5-5", label: "Claude Opus 5.5", note: "Best judgment. About $4 / $20 per million tokens in / out." },
  { id: "claude-sonnet-5-5", label: "Claude Sonnet 5.5", note: "Fast and capable, about half the cost." },
  { id: "claude-haiku-5-5", label: "Claude Haiku 5.5", note: "Cheapest. Fine for scoring; weaker at writing." },
];

const MODES = [
  { id: "auto", title: "Submit when everything checks out",
    body: "Submits on its own only if the fit score is high enough, every answer passed review, the site allows it, and you're under the daily limit. Anything else waits for you." },
  { id: "approve", title: "Always ask me first", body: "Fills everything in, then waits at the submit button for you." },
  { id: "draft", title: "Never submit", body: "Fills forms and prepares materials. You submit everything yourself." },
] as const;

export default function SettingsPage() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [draft, setDraft] = useState<Settings | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    api<SettingsView>("/api/settings")
      .then((v) => { setView(v); setDraft(v.settings); })
      .catch((e) => setLoadError(`Can't reach the API. Start ApplyPilot first. (${e.message})`));
  }, []);

  const dirty = !!view && !!draft && JSON.stringify(draft) !== JSON.stringify(view.settings);
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  if (loadError) return <p className="notice notice-error" role="alert">{loadError}</p>;
  if (!view || !draft) return <p className="muted">Loading settings…</p>;

  const s = draft;
  const update = (fn: (d: Settings) => void) => {
    const next = structuredClone(s);
    fn(next);
    setDraft(next);
    setMessage(null);
  };
  const e = (path: string) => errors[path];

  async function save() {
    setSaving(true);
    setErrors({});
    try {
      const v = await api<SettingsView>("/api/settings", { method: "PUT", body: JSON.stringify(s) });
      setView(v);
      setDraft(v.settings);
      setMessage("Saved. Changes apply from the next search or application.");
    } catch (err) {
      if (err instanceof ValidationError) {
        setErrors(err.fields);
        setMessage(`Fix ${Object.keys(err.fields).length} setting${Object.keys(err.fields).length > 1 ? "s" : ""} marked below.`);
      } else setMessage((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="profile">
      <div className="profile-head">
        <h1>Settings</h1>
        <p className="lede">Where to look, how picky to be, and how much the agent may do on its own.</p>
      </div>

      <ApiKeySection isSet={view.api_key_set} onChange={(v) => { setView({ ...view, api_key_set: v.api_key_set }); }} />

      <section className="panel" aria-labelledby="boards-title">
        <h2 id="boards-title">Companies to watch</h2>
        <p className="section-lede">
          ApplyPilot reads companies’ own job boards on Greenhouse, Lever and Ashby. Add a company by name, or paste a
          link to its careers page or any of its job postings.
        </p>
        <BoardAdder watching={(r) => !!r.source && s.sources[r.source].includes(r.token)}
                    onAdd={(r) => update((d) => {
          if (r.source && !d.sources[r.source].includes(r.token)) d.sources[r.source].push(r.token);
        })} />
        {(Object.keys(SOURCE_LABEL) as Source[]).every((k) => s.sources[k].length === 0) ? (
          <p className="notice">No companies yet. Add a few you’d like to work at.</p>
        ) : (
          <ul className="boards">
            {(Object.keys(SOURCE_LABEL) as Source[]).flatMap((src) => s.sources[src].map((token, i) => (
              <li key={`${src}:${token}`} className={e(`sources.${src}.${i}`) ? "has-error" : ""}>
                <strong>{token}</strong>
                <span className="muted">{SOURCE_LABEL[src]}</span>
                {e(`sources.${src}.${i}`) && <span className="field-error">Not a valid board name.</span>}
                <button type="button" className="quiet small" aria-label={`Stop watching ${token}`}
                        onClick={() => update((d) => { d.sources[src] = d.sources[src].filter((t) => t !== token); })}>
                  Remove
                </button>
              </li>
            )))}
          </ul>
        )}
      </section>

      <section className="panel" aria-labelledby="search-title">
        <h2 id="search-title">Searching and scoring</h2>
        <Toggle label="Search for new jobs automatically" checked={s.schedule.enabled}
                onChange={(v) => update((d) => { d.schedule.enabled = v; })} />
        {s.schedule.enabled && (
          <div className="field">
            <label htmlFor="every">How often</label>
            <select id="every" value={s.schedule.sweep_every_hours}
                    onChange={(ev) => update((d) => { d.schedule.sweep_every_hours = Number(ev.target.value); })}>
              {[3, 6, 12, 24, 48].map((h) => <option key={h} value={h}>Every {h} hours</option>)}
            </select>
            <p className="field-hint">Runs while ApplyPilot is open. Each search scores new jobs with the AI model below.</p>
          </div>
        )}
        <fieldset className="radio-group">
          <legend>AI model</legend>
          {MODELS.map((m) => (
            <label key={m.id} className="choice">
              <input type="radio" name="model" checked={s.matcher.model === m.id}
                     onChange={() => update((d) => { d.matcher.model = m.id; })} />
              <span><strong>{m.label}</strong><span className="choice-note">{m.note}</span></span>
            </label>
          ))}
        </fieldset>
        <div className="grid">
          <NumberField label="Show jobs scoring at least" value={s.matcher.min_score_to_draft}
                       error={e("matcher.min_score_to_draft")} hint="Out of 100. Lower ones are kept but marked below threshold."
                       onChange={(v) => update((d) => { d.matcher.min_score_to_draft = v ?? 0; })} />
          <NumberField label="Most jobs to score per search" value={s.matcher.max_scored_per_sweep}
                       error={e("matcher.max_scored_per_sweep")} hint="Caps cost; the rest are scored next time."
                       onChange={(v) => update((d) => { d.matcher.max_scored_per_sweep = v ?? 1; })} />
        </div>
      </section>

      <section className="panel" aria-labelledby="apply-title">
        <h2 id="apply-title">Applying</h2>
        <fieldset className="radio-group">
          <legend>When a form is ready</legend>
          {MODES.map((m) => (
            <label key={m.id} className="choice">
              <input type="radio" name="mode" checked={s.applicant.mode === m.id}
                     onChange={() => update((d) => { d.applicant.mode = m.id; })} />
              <span><strong>{m.title}</strong><span className="choice-note">{m.body}</span></span>
            </label>
          ))}
        </fieldset>
        <div className="grid">
          {s.applicant.mode === "auto" && (
            <NumberField label="Submit on its own only at a fit score of" value={s.applicant.auto_submit_min_score}
                         error={e("applicant.auto_submit_min_score")} hint="Out of 100."
                         onChange={(v) => update((d) => { d.applicant.auto_submit_min_score = v ?? 0; })} />
          )}
          <NumberField label="Most applications per day" value={s.applicant.daily_submit_cap}
                       error={e("applicant.daily_submit_cap")} hint="Applies to every mode."
                       onChange={(v) => update((d) => { d.applicant.daily_submit_cap = v ?? 0; })} />
        </div>
      </section>

      <BrowserSection settings={s} update={update} />

      <div className={`savebar${dirty || message ? " is-visible" : ""}`} role="region" aria-label="Save settings">
        <span aria-live="polite">{message ?? (dirty ? "You have unsaved changes." : "")}</span>
        <div>
          {dirty && <button type="button" className="quiet" onClick={() => { setDraft(view.settings); setErrors({}); setMessage(null); }}>Discard</button>}
          <button type="button" onClick={save} disabled={!dirty || saving}>{saving ? "Saving…" : "Save settings"}</button>
        </div>
      </div>
    </div>
  );
}

function ApiKeySection({ isSet, onChange }: { isSet: boolean; onChange: (v: SettingsView) => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ text: string; bad?: boolean } | null>(null);

  async function saveKey() {
    setBusy(true);
    setMsg(null);
    try {
      onChange(await api<SettingsView>("/api/settings/api-key", { method: "PUT", body: JSON.stringify({ key }) }));
      setKey("");
      setMsg({ text: "Key checked with Anthropic and saved on this computer." });
    } catch (e) {
      setMsg({ text: (e as Error).message, bad: true });
    } finally {
      setBusy(false);
    }
  }

  async function removeKey() {
    setBusy(true);
    try {
      onChange(await api<SettingsView>("/api/settings/api-key", { method: "DELETE" }));
      setMsg({ text: "Key removed." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="key-title">
      <h2 id="key-title">Anthropic API key</h2>
      <p className="section-lede">
        Scoring jobs, drafting answers and tailoring resumes use Claude. Create a key in the Anthropic Console under
        API Keys. It’s saved only on this computer, in the git-ignored <code>.env</code> file, and is never shown again.
      </p>
      <p className={`key-status${isSet ? " is-set" : ""}`}>{isSet ? "A key is set." : "No key yet."}</p>
      <form className="key-row" onSubmit={(ev) => { ev.preventDefault(); if (key) saveKey(); }}>
        <label htmlFor="api-key" className="sr-only">API key</label>
        <input id="api-key" type="password" autoComplete="off" spellCheck={false} placeholder="sk-ant-…"
               value={key} onChange={(ev) => setKey(ev.target.value)} />
        <button type="submit" disabled={!key || busy}>{busy ? "Checking…" : isSet ? "Replace key" : "Save key"}</button>
        {isSet && <button type="button" className="quiet" onClick={removeKey} disabled={busy}>Remove</button>}
      </form>
      {msg && <p className={msg.bad ? "field-error" : "field-hint"} role={msg.bad ? "alert" : undefined}>{msg.text}</p>}
    </section>
  );
}

function BoardAdder({ onAdd, watching }: { onAdd: (r: BoardResult) => void; watching: (r: BoardResult) => boolean }) {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<BoardResult | null>(null);

  async function check() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api<BoardResult>("/api/settings/check-board", { method: "POST", body: JSON.stringify({ input }) });
      if (r.source && r.message.startsWith("Found") && watching(r)) {
        setResult({ ...r, message: `Already watching ${r.token}.` });
      } else {
        setResult(r);
        if (r.source && r.message.startsWith("Found")) onAdd(r);
      }
      if (r.source) setInput("");
    } catch (e) {
      setResult({ source: null, token: "", jobs: 0, message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <form className="key-row" onSubmit={(ev) => { ev.preventDefault(); if (input.trim()) check(); }}>
        <label htmlFor="board" className="sr-only">Company name or careers link</label>
        <input id="board" placeholder="Company name, or a link like boards.greenhouse.io/stripe"
               value={input} onChange={(ev) => setInput(ev.target.value)} />
        <button type="submit" disabled={!input.trim() || busy}>{busy ? "Looking…" : "Add company"}</button>
      </form>
      {result && (
        <p className={result.source ? "field-hint" : "field-error"} role="status">
          {result.message.startsWith("Found") ? `Added ${result.token}. ${result.message}` : result.message}
        </p>
      )}
    </>
  );
}

function BrowserSection({ settings, update }: { settings: Settings; update: (fn: (d: Settings) => void) => void }) {
  const [desk, setDesk] = useState<Desktop | null>(null);

  const poll = useCallback(() => api<Desktop>("/api/desktop").then(setDesk).catch(() => {}), []);
  useEffect(() => {
    poll();
    const t = setInterval(poll, desk?.busy ? 2000 : 8000);
    return () => clearInterval(t);
  }, [poll, desk?.busy]);

  async function act(action: "start" | "stop") {
    try {
      setDesk(await api<Desktop>(`/api/desktop/${action}`, { method: "POST" }));
    } catch (e) {
      setDesk((d) => d && { ...d, message: (e as Error).message });
    }
  }

  const docker = settings.automation.backend === "docker";
  return (
    <section className="panel" aria-labelledby="browser-title">
      <h2 id="browser-title">The agent’s browser</h2>
      <fieldset className="radio-group">
        <legend>Where the agent works</legend>
        <label className="choice">
          <input type="radio" name="backend" checked={docker}
                 onChange={() => update((d) => { d.automation.backend = "docker"; })} />
          <span><strong>Its own computer</strong>
            <span className="choice-note">A separate desktop in Docker that you watch in the dashboard. Keeps the agent away from your own browser.</span></span>
        </label>
        <label className="choice">
          <input type="radio" name="backend" checked={!docker}
                 onChange={() => update((d) => { d.automation.backend = "local"; })} />
          <span><strong>This computer</strong>
            <span className="choice-note">Opens a visible Chrome window here. No Docker needed, but you can’t watch it from the dashboard.</span></span>
        </label>
      </fieldset>

      {docker && desk && (
        <div className="desktop-row">
          <span className={`dot${desk.running ? " is-on" : ""}`} aria-hidden="true" />
          <span>{desk.running ? "Running" : "Stopped"}{desk.busy || desk.message ? `: ${desk.message}` : ""}</span>
          {desk.running
            ? <button type="button" className="quiet small" disabled={desk.busy} onClick={() => act("stop")}>Stop</button>
            : <button type="button" className="small" disabled={desk.busy} onClick={() => act("start")}>{desk.busy ? "Starting…" : "Start"}</button>}
        </div>
      )}

      <TagInput label="Sites where I accept the risk of automating anyway"
                values={settings.automation.accept_tos_risk_for} placeholder="linkedin.com"
                hint="LinkedIn, Indeed and Glassdoor forbid bots and restrict accounts that use them. The agent hands those sites to you. Listing one here lets it fill forms there anyway; you’ll still be warned and still submit by hand."
                onChange={(v) => update((d) => { d.automation.accept_tos_risk_for = v.map((x) => x.toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").split("/")[0]); })} />
    </section>
  );
}
