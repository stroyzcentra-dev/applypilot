export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8765";

export type PolicyLevel = "allowed" | "caution" | "prohibited";

export type Job = {
  id: string;
  company: string;
  title: string;
  location: string;
  url: string;
  apply_url: string;
  status: string;
  score: number | null;
  summary: string | null;
  policy: { level: PolicyLevel; action: string; warning: string | null };
};

export type Mode = "agent" | "paused" | "user" | "waiting_approval" | "done";

export type Run = {
  id: string;
  job_id: string;
  started_at: string;
  mode: Mode;
  outcome: string | null;
  warning: string | null;
  needs_input: string[];
};

export type Step = {
  seq: number;
  at: string;
  kind:
    | "action" | "info" | "warning" | "handoff" | "needs_input" | "status" | "error"
    | "draft" | "draft_failed";
  title: string;
  detail: string;
  url: string | null;
  screenshot: string | null;
  sentences: { text: string; sources: string[] }[] | null;
  issues: string[] | null;
  file: string | null;
};

export type Config = {
  backend: "docker" | "local";
  novnc_url: string;
  mode: "auto" | "approve" | "draft";
  drafting: boolean;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `${res.status} ${res.statusText}`);
  }
  return res.json();
}

export const control = (runId: string, action: string) =>
  api<Run>(`/api/runs/${runId}/control`, { method: "POST", body: JSON.stringify({ action }) });
