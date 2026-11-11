"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, type Job, type Run } from "@/lib/api";

const POLICY_TEXT = {
  allowed: "Agent can apply",
  caution: "Agent fills, you submit",
  prohibited: "You apply; agent stays off",
} as const;

export default function JobsPage() {
  const router = useRouter();
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState<string | null>(null);

  useEffect(() => {
    api<Job[]>("/api/jobs")
      .then(setJobs)
      .catch((e) => setError(`Can't reach the API. Start it with \`applypilot serve\`. (${e.message})`));
  }, []);

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

  return (
    <section className="jobs">
      <h1>Jobs worth applying to</h1>
      <p className="lede">
        Ranked by fit score. Starting an application opens the employer’s form in the agent’s browser,
        where you can watch it work.
      </p>

      {error && <p className="notice notice-error" role="alert">{error}</p>}
      {jobs === null && !error && <p className="muted">Loading jobs…</p>}
      {jobs?.length === 0 && (
        <p className="notice">No jobs yet. Run <code>applypilot sweep</code> to find some.</p>
      )}

      <ol className="job-list">
        {jobs?.map((job) => (
          <li key={job.id} className="job">
            <span className="score" aria-label={`Fit score ${job.score ?? "not scored"}`}>
              {job.score ?? "–"}
            </span>
            <div className="job-main">
              <h2>
                <a href={job.url} target="_blank" rel="noreferrer">{job.title}</a>
              </h2>
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
              <button onClick={() => start(job)} disabled={starting !== null}>
                {starting === job.id ? "Opening…" : "Start application"}
              </button>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
