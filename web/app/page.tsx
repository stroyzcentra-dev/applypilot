"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api, type Job, type Run, type Sweep } from "@/lib/api";

const POLICY_TEXT = {
  allowed: "Agent can apply",
  caution: "Agent fills, you submit",
  prohibited: "You apply; agent stays off",
} as const;

const GROUPS = {
  todo: { label: "To apply", statuses: ["discovered", "scored", "below_threshold", "drafting", "awaiting_approval", "failed"] },
  applied: { label: "Applied", statuses: ["submitting", "submitted", "interviewing", "offer", "rejected", "withdrawn"] },
  skipped: { label: "Skipped", statuses: ["filtered_out"] },
} as const;
type Group = keyof typeof GROUPS;

const STARTABLE = new Set(["discovered", "scored", "below_threshold", "failed", "drafting", "awaiting_approval"]);

export default function JobsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [sweep, setSweep] = useState<Sweep | null>(null);
  const [group, setGroup] = useState<Group>("todo");
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  const loadJobs = useCallback(() => {
    api<Job[]>("/api/jobs?show=all")
      .then(setJobs)
      .catch((e) => setError(`Can't reach ApplyPilot. Start it first. (${e.message})`));
  }, []);

  useEffect(loadJobs, [loadJobs]);

  // Poll the search status; refresh the list when a search finishes.
  useEffect(() => {
    let wasRunning = false;
    const tick = () => api<Sweep>("/api/sweeps/current").then((s) => {
      setSweep(s);
      if (wasRunning && !s.running) loadJobs();
      wasRunning = s.running;
    }).catch(() => {});
    tick();
    const t = setInterval(tick, 2000);
    return () => clearInterval(t);
  }, [loadJobs]);

  async function findJobs() {
    setError(null);
    try {
      setSweep(await api<Sweep>("/api/sweeps", { method: "POST" }));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function start(job: Job) {
    setStarting(job.id);
    setError(null);
    try {
      const run = await api<Run>("/api/runs", { method: "POST", body: JSON.stringify({ job_id: job.id }) });
      router.push(`/run/${run.id}`);
    } catch (e) {
      setError((e as Error).message);
      setStarting(null);
    }
  }

  const shown = (jobs ?? []).filter((j) => (GROUPS[group].statuses as readonly string[]).includes(j.status));
  const count = (g: Group) => (jobs ?? []).filter((j) => (GROUPS[g].statuses as readonly string[]).includes(j.status)).length;

  return (
    <section className="jobs">
      <div className="jobs-head">
        <div>
          <h1>Jobs</h1>
          <p className="lede">Ranked by how well they fit your profile. Open one to see why.</p>
        </div>
      </div>

      <div className={`search-bar${sweep?.running ? " is-running" : ""}`}>
        <button onClick={findJobs} disabled={!!sweep?.running}>{sweep?.running ? "Searching…" : "Find jobs"}</button>
        <span className="muted" aria-live="polite">{sweepText(sweep)}</span>
        <div className="filters" role="group" aria-label="Show">
          {(Object.keys(GROUPS) as Group[]).map((g) => (
            <button key={g} type="button" className="quiet small" aria-pressed={group === g} onClick={() => setGroup(g)}>
              {GROUPS[g].label} {jobs ? count(g) : ""}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="notice notice-error" role="alert">{error}</p>}
      {jobs === null && !error && <p className="muted">Loading jobs…</p>}
      {jobs?.length === 0 && (
        <p className="notice">
          No jobs yet. Add companies in <Link href="/settings">Settings</Link>, then press Find jobs.
        </p>
      )}
      {jobs && jobs.length > 0 && shown.length === 0 && <p className="muted">Nothing here.</p>}

      <ol className="job-list">
        {shown.map((job) => (
          <li key={job.id} className="job">
            <span className="score" aria-label={`Fit score ${job.score ?? "not scored"}`}>{job.score ?? "–"}</span>
            <div className="job-main">
              <h2><Link className="title-link" href={`/job?id=${encodeURIComponent(job.id)}`}>{job.title}</Link></h2>
              <p className="job-meta">
                {job.company}, {job.location || "location not listed"}
                <span className="status">{job.status.replace(/_/g, " ")}</span>
              </p>
              {job.summary && <p className="job-summary">{job.summary}</p>}
            </div>
            <div className="job-actions">
              <span className={`policy policy-${job.policy.level}`} title={job.policy.warning ?? undefined}>
                {POLICY_TEXT[job.policy.level]}
              </span>
              {STARTABLE.has(job.status) && (
                <button onClick={() => start(job)} disabled={starting !== null}>
                  {starting === job.id ? "Opening…" : "Start application"}
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function ago(iso: string) {
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} days ago`;
}

function until(iso: string) {
  const mins = Math.round((new Date(iso).getTime() - Date.now()) / 60000);
  if (mins <= 1) return "in a moment";
  return mins < 60 ? `in ${mins} min` : `in ${Math.round(mins / 60)} h`;
}

function sweepText(s: Sweep | null): string {
  if (!s) return "";
  if (s.running) return `${s.progress}…`;
  const parts: string[] = [];
  if (s.error) parts.push(`Last search failed: ${s.error}`);
  else if (s.stats && s.finished_at) {
    const st = s.stats;
    parts.push(`Last search ${ago(s.finished_at)}: ${st.new} new, ${st.ready} good matches, ${st.filtered_out} skipped by your filters`);
    if (!s.scoring && st.deferred) parts.push(`${st.deferred} waiting to be scored (add an API key in Settings)`);
    const failed = Object.keys(st.source_errors);
    if (failed.length) parts.push(`couldn't read ${failed.join(", ")}`);
  } else parts.push("No searches yet.");
  if (s.next_scheduled) parts.push(`next search ${until(s.next_scheduled)}`);
  return parts.join("; ") + ".";
}
