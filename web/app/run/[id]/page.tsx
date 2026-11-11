"use client";

import { use, useEffect, useRef, useState } from "react";
import { API, api, control, type Config, type Mode, type Run, type Step } from "@/lib/api";

const DRIVER: Record<Mode, { label: string; hint: string }> = {
  agent: { label: "The agent is driving", hint: "Pause or take over at any time." },
  paused: { label: "Paused", hint: "Nobody is acting. Resume when you're ready." },
  user: { label: "You're driving", hint: "Click and type in the screen below. The agent is waiting." },
  waiting_approval: { label: "Waiting for your decision", hint: "Review the form, then choose below." },
  done: { label: "Finished", hint: "This run is over. The browser stays open for you." },
};

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [run, setRun] = useState<Run | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const timelineEnd = useRef<HTMLLIElement>(null);

  useEffect(() => {
    api<Config>("/api/config").then(setConfig).catch((e) => setError(e.message));
  }, []);

  // Timeline: server-sent events, history first, then live.
  useEffect(() => {
    const es = new EventSource(`${API}/api/runs/${id}/events`);
    es.onmessage = (e) => {
      const step: Step = JSON.parse(e.data);
      setSteps((prev) => (prev.some((s) => s.seq === step.seq) ? prev : [...prev, step]));
    };
    es.addEventListener("end", () => es.close());
    return () => es.close();
  }, [id]);

  // Run state (who is driving) changes with user actions and agent progress.
  useEffect(() => {
    let stop = false;
    const tick = () =>
      api<Run>(`/api/runs/${id}`)
        .then((r) => !stop && setRun(r))
        .catch((e) => !stop && setError(e.message));
    tick();
    const t = setInterval(tick, 1200);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [id]);

  // Braces matter: scrollIntoView returns a Promise in newer browsers, and an effect
  // may only return a cleanup function.
  useEffect(() => {
    timelineEnd.current?.scrollIntoView({ block: "nearest" });
  }, [steps.length]);

  async function act(action: string) {
    setBusy(true);
    setError(null);
    try {
      setRun(await control(id, action));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const handedOff = steps.some((s) => s.kind === "handoff");
  // A handed-off run waits for your decision too, but the right message is that you're driving.
  const rawMode = run?.mode ?? "agent";
  const mode: Mode = handedOff && rawMode === "waiting_approval" ? "user" : rawMode;
  const title = steps.find((s) => s.kind === "status")?.title.replace(/^Applying: /, "") ?? "Application";

  return (
    <section className="run">
      <div className="run-head">
        <h1>{title}</h1>
        {error && <p className="notice notice-error" role="alert">{error}</p>}
      </div>

      <div className={`driver driver-${mode}`} role="status" aria-live="polite">
        <div>
          <strong>{DRIVER[mode].label}</strong>
          <span>{handedOff && mode === "user" ? "The agent won't act on this site." : DRIVER[mode].hint}</span>
        </div>
        <div className="driver-controls">
          {mode === "agent" && (
            <>
              <button className="quiet" onClick={() => act("pause")} disabled={busy}>Pause</button>
              <button onClick={() => act("take_over")} disabled={busy}>Take over</button>
            </>
          )}
          {(mode === "paused" || (mode === "user" && !handedOff)) && (
            <button onClick={() => act("hand_back")} disabled={busy}>Hand back to agent</button>
          )}
          {mode !== "done" && (
            <button className="quiet" onClick={() => act("cancel")} disabled={busy}>Stop run</button>
          )}
        </div>
      </div>

      {run?.warning && (
        <p className={`notice ${handedOff ? "notice-error" : "notice-caution"}`} role="alert">
          {linkify(run.warning)}
        </p>
      )}

      {(mode === "waiting_approval" || (handedOff && mode !== "done")) && (
        <div className="decision">
          <p>
            {handedOff
              ? "Apply on this site yourself. Then record it here so your application log stays accurate."
              : run?.needs_input.length
                ? `Answer ${run.needs_input.length} required field${run.needs_input.length > 1 ? "s" : ""} in the screen below first.`
                : "Every required field has a value. Check them, then choose how to submit."}
          </p>
          <div className="decision-buttons">
            {handedOff ? (
              <button className="quiet" onClick={() => act("open_page")} disabled={busy}>Open page</button>
            ) : config?.mode !== "draft" ? (
              <button onClick={() => act("agent_submit")} disabled={busy}>Submit for me</button>
            ) : null}
            <button className={handedOff ? "" : "quiet"} onClick={() => act("user_submitted")} disabled={busy}>
              I submitted it
            </button>
          </div>
        </div>
      )}

      <div className="workspace">
        <div className="screen">
          {config ? (
            <iframe src={config.novnc_url} title="The agent's browser" allow="clipboard-read; clipboard-write" />
          ) : (
            <p className="muted">Connecting to the agent’s screen…</p>
          )}
        </div>

        <ol className="timeline" aria-label="What the agent has done">
          {steps.map((s) => (
            <li key={s.seq} className={`step step-${s.kind}`} ref={s === steps.at(-1) ? timelineEnd : undefined}>
              <span className="step-n">{s.seq}</span>
              <div>
                <p className="step-title">{s.title}</p>
                {s.sentences && s.detail && s.file && <p className="step-detail">{s.detail}</p>}
                {s.file && (
                  <p className="step-detail">
                    <a href={`${API}/resumes/${encodeURI(s.file)}`} target="_blank" rel="noreferrer">
                      Open the PDF
                    </a>
                  </p>
                )}
                {s.sentences ? (
                  <Draft sentences={s.sentences} issues={s.issues} />
                ) : (
                  s.detail && <p className="step-detail">{s.detail}</p>
                )}
                {s.screenshot && (
                  <a href={`${API}/screenshots/${s.screenshot}`} target="_blank" rel="noreferrer">
                    <img src={`${API}/screenshots/${s.screenshot}`} alt={`Screen after: ${s.title}`} loading="lazy" />
                  </a>
                )}
              </div>
            </li>
          ))}
          {steps.length === 0 && <li className="muted">Waiting for the first step…</li>}
        </ol>
      </div>
    </section>
  );
}

// A drafted answer: each sentence with the sources it rests on, so you can see
// exactly why the agent believes it can say that.
function Draft({ sentences, issues }: { sentences: { text: string; sources: string[] }[]; issues: string[] | null }) {
  return (
    <div className="draft">
      {sentences.map((s, i) => (
        <p key={i} className="draft-sentence">
          {s.text}
          <span className="draft-sources">
            {s.sources.map((src) => (
              <span key={src} className="source" title={src}>{sourceLabel(src)}</span>
            ))}
          </span>
        </p>
      ))}
      {issues && issues.length > 0 && (
        <ul className="draft-issues">
          {issues.map((m, i) => <li key={i}>{m}</li>)}
        </ul>
      )}
    </div>
  );
}

function sourceLabel(src: string) {
  const [kind, ...rest] = src.split(":");
  const ref = rest.join(":");
  if (kind === "posting") return "posting";
  if (kind === "answer") return ref.replace(/_/g, " ");
  if (kind === "summary") return "from cited facts";
  return ref;
}

function linkify(text: string) {
  const [before, url] = text.split(/Terms: (https?:\/\/\S+)/);
  return url ? (
    <>
      {before}
      <a href={url} target="_blank" rel="noreferrer">Read their terms</a>.
    </>
  ) : (
    text
  );
}
