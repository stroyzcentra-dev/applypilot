"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { API, api, type Job, type Run } from "@/lib/api";

type Requirement = { requirement: string; status: "met" | "partial" | "missing"; fact_ids: string[]; note: string };
type JobDetail = Job & {
  description: string;
  salary: [number | null, number | null];
  assessment: { score: number; verdict: string; summary: string; requirements: Requirement[]; dealbreakers: string[] } | null;
  events: { at: string; from_status: string | null; to_status: string; actor: string; note: string }[];
  can_mark: string[];
};

const MARK_LABEL: Record<string, string> = {
  submitted: "I applied", interviewing: "Interviewing", offer: "Got an offer", rejected: "Rejected", withdrawn: "Not interested",
};
const STARTABLE = new Set(["discovered", "scored", "below_threshold", "failed", "drafting", "awaiting_approval"]);

export default function JobPage() {
  return <Suspense fallback={<p className="muted">Loading…</p>}><JobView /></Suspense>;
}

function JobView() {
  const id = useSearchParams().get("id") ?? "";
  const router = useRouter();
  const [job, setJob] = useState<JobDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [resume, setResume] = useState<{ file: string; bullets: number; unverified: string[] } | null>(null);

  const load = useCallback(() => {
    api<JobDetail>(`/api/job?id=${encodeURIComponent(id)}`).then(setJob).catch((e) => setError(e.message));
  }, [id]);
  useEffect(load, [load]);

  async function mark(status: string) {
    setBusy(status);
    setError(null);
    try {
      setJob(await api<JobDetail>("/api/job/status", { method: "POST", body: JSON.stringify({ id, status }) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function tailor() {
    setBusy("resume");
    setError(null);
    try {
      setResume(await api("/api/job/resume", { method: "POST", body: JSON.stringify({ id }) }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    setBusy("apply");
    try {
      const run = await api<Run>("/api/runs", { method: "POST", body: JSON.stringify({ job_id: id }) });
      router.push(`/run/${run.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(null);
    }
  }

  if (error && !job) return <p className="notice notice-error" role="alert">{error}</p>;
  if (!job) return <p className="muted">Loading…</p>;
  const a = job.assessment;
  const [lo, hi] = job.salary;

  return (
    <div className="profile">
      <p><Link href="/">Back to jobs</Link></p>
      <div className="profile-head">
        <h1>{job.title}</h1>
        <p className="lede">
          {job.company}, {job.location || "location not listed"}
          {(lo || hi) && `. $${(lo ?? 0).toLocaleString()} to $${(hi ?? 0).toLocaleString()}`}.{" "}
          <a href={job.url} target="_blank" rel="noreferrer">View the posting</a>
        </p>
        {job.policy.warning && <p className={`notice ${job.policy.level === "prohibited" ? "notice-error" : "notice-caution"}`}>{job.policy.warning}</p>}
      </div>

      {error && <p className="notice notice-error" role="alert">{error}</p>}

      <section className="panel">
        <div className="mark-row">
          {STARTABLE.has(job.status) && (
            <button onClick={apply} disabled={busy !== null}>{busy === "apply" ? "Opening…" : "Start application"}</button>
          )}
          <button className="quiet" onClick={tailor} disabled={busy !== null}>
            {busy === "resume" ? "Tailoring (about a minute)…" : "Make a tailored resume"}
          </button>
          {resume && (
            <a href={`${API}/resumes/${encodeURI(resume.file)}`} target="_blank" rel="noreferrer">
              Open the PDF ({resume.bullets} bullets{resume.unverified.length ? `, ${resume.unverified.length} in your own wording` : ""})
            </a>
          )}
        </div>
        <h3>Status: {job.status.replace(/_/g, " ")}</h3>
        {job.can_mark.length > 0 && (
          <div className="mark-row">
            <span className="muted">Record an update:</span>
            {job.can_mark.map((s) => (
              <button key={s} className="quiet small" onClick={() => mark(s)} disabled={busy !== null}>{MARK_LABEL[s] ?? s}</button>
            ))}
          </div>
        )}
      </section>

      <section className="panel" aria-labelledby="fit-title">
        <h2 id="fit-title">{a ? `Fit: ${a.score} out of 100` : "Not scored yet"}</h2>
        {a ? (
          <>
            <p className="section-lede">{a.summary}</p>
            {a.dealbreakers.length > 0 && (
              <p className="notice notice-error">Dealbreakers: {a.dealbreakers.join("; ")}</p>
            )}
            <ul className="reqs">
              {a.requirements.map((r, i) => (
                <li key={i}>
                  <span className={`req-status req-${r.status}`}>{r.status === "met" ? "Have it" : r.status === "partial" ? "Partly" : "Missing"}</span>
                  <div>
                    {r.requirement}
                    {r.fact_ids.length > 0 && (
                      <span className="draft-sources">{r.fact_ids.map((f) => <span key={f} className="source">{f}</span>)}</span>
                    )}
                    <p className="req-note">{r.note}</p>
                  </div>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="section-lede">Press Find jobs on the Jobs page with an API key set, and new jobs are scored against your profile.</p>
        )}
      </section>

      <section className="panel" aria-labelledby="log-title">
        <h2 id="log-title">History</h2>
        <ol className="log">
          {job.events.map((e, i) => (
            <li key={i}>
              <time dateTime={e.at}>{new Date(e.at).toLocaleString()}</time>
              <span><strong>{e.to_status.replace(/_/g, " ")}</strong> by {e.actor === "user" ? "you" : e.actor}{e.note ? `: ${e.note}` : ""}</span>
            </li>
          ))}
        </ol>
      </section>

      {job.description && (
        <section className="panel" aria-labelledby="desc-title">
          <h2 id="desc-title">The posting</h2>
          <div className="description">{job.description}</div>
        </section>
      )}
    </div>
  );
}
