"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";

type Status = {
  backend: "docker" | "local";
  desktop_running: boolean;
  api_key_set: boolean;
  example_profile: boolean;
  facts: number;
  jobs: number;
  mode: "auto" | "approve" | "draft";
};

const SEEN_KEY = "applypilot.guide.seen.v1";

// localStorage can be unavailable (private windows, blocked storage); the guide still works.
const readSeen = () => {
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false;
  }
};
const markSeen = () => {
  try {
    localStorage.setItem(SEEN_KEY, "1");
  } catch {
    /* ignore */
  }
};

const MODE_TEXT = {
  auto: "submits on its own only when every check passes; otherwise it waits for you",
  approve: "always stops at the submit button for you",
  draft: "fills forms but never submits",
} as const;

export function Guide() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [apiDown, setApiDown] = useState(false);

  const refresh = useCallback(() => {
    api<Status>("/api/status")
      .then((s) => {
        setStatus(s);
        setApiDown(false);
      })
      .catch(() => setApiDown(true));
  }, []);

  const open = useCallback(() => {
    refresh();
    dialog.current?.showModal();
  }, [refresh]);

  useEffect(() => {
    if (!readSeen()) open();
  }, [open]);

  function close() {
    markSeen();
    dialog.current?.close();
  }

  const steps: { done: boolean | null; title: string; how: React.ReactNode }[] = [
    {
      done: apiDown ? false : status ? true : null,
      title: "Start the API",
      how: <>In the project folder, run <code>uv run applypilot serve</code>. This page talks to it.</>,
    },
    {
      done: status ? status.desktop_running : null,
      title: "Start the agent’s computer",
      how:
        status?.backend === "local" ? (
          <>You’re using the local browser, so there’s nothing to start.</>
        ) : (
          <>Run <code>uv run applypilot desktop up</code> (needs Docker). The first run builds it.</>
        ),
    },
    {
      done: status ? !status.example_profile : null,
      title: "Fill in your profile",
      how: !status ? (
        <>Every claim the agent makes comes from this file.</>
      ) : status.example_profile ? (
        <>You’re still on the example profile. Open <a href="/profile">Profile</a> to import your resume
          or add your experience as short, specific facts.</>
      ) : (
        <>{status.facts} facts loaded. The agent can only say what these facts say.</>
      ),
    },
    {
      done: status ? status.api_key_set : null,
      title: "Add your Anthropic API key",
      how: (
        <>Put it in <code>.env</code> as <code>ANTHROPIC_API_KEY=…</code>. It stays on your machine:
          the file is git-ignored and the key is never shown here or sent to this page.</>
      ),
    },
    {
      done: status ? status.jobs > 0 : null,
      title: "Find jobs",
      how: status?.jobs ? (
        <>{status.jobs} jobs found. Run <code>uv run applypilot sweep</code> again any time.</>
      ) : (
        <>Add company job boards to <code>settings.yaml</code>, then run <code>uv run applypilot sweep</code>.</>
      ),
    },
  ];

  return (
    <>
      <button className="link-button" onClick={open}>How it works</button>
      <dialog ref={dialog} className="guide" aria-labelledby="guide-title" onClose={markSeen}>
        {/* Focus starts on the title so the guide opens at the top, not at its last button. */}
        <h2 id="guide-title" tabIndex={-1} autoFocus>Welcome to ApplyPilot</h2>
        <p className="guide-lede">
          An agent that applies to jobs in a browser you can watch. It only says what your profile
          supports, and it hands the browser to you whenever a decision should be yours.
        </p>

        <h3>Set up</h3>
        <ol className="checklist">
          {steps.map((s) => (
            <li key={s.title} className={s.done === null ? "" : s.done ? "is-done" : "is-todo"}>
              <span className="check" aria-label={s.done === null ? "checking" : s.done ? "done" : "to do"}>
                {s.done === null ? "…" : s.done ? "✓" : ""}
              </span>
              <div>
                <strong>{s.title}</strong>
                <p>{s.how}</p>
              </div>
            </li>
          ))}
        </ol>

        <h3>During an application</h3>
        <ul className="guide-points">
          <li>
            <strong>The banner says who’s driving.</strong> Press <em>Take over</em> to use the mouse and
            keyboard in the live screen yourself; the agent waits until you press <em>Hand back</em>.
          </li>
          <li>
            <strong>It never guesses.</strong> Questions it can’t answer from your profile are listed for
            you. Every drafted sentence shows the fact it came from.
          </li>
          <li>
            <strong>Some sites don’t allow bots.</strong> On LinkedIn, Indeed and Glassdoor it warns you,
            stays off the site, and hands the browser to you.
          </li>
          <li>
            <strong>Submitting:</strong> you’re in <em>{status?.mode ?? "auto"}</em> mode, so it{" "}
            {MODE_TEXT[status?.mode ?? "auto"]}. Change this in <code>settings.yaml</code>.
          </li>
        </ul>

        <div className="guide-actions">
          <button className="quiet" onClick={refresh}>Check again</button>
          <button onClick={close}>Got it</button>
        </div>
      </dialog>
    </>
  );
}
