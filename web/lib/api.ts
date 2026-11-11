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

// ---- profile -------------------------------------------------------------------

export type FactKind = "experience" | "achievement" | "project" | "skill" | "education" | "other";

export type Fact = {
  id: string;
  kind: FactKind;
  text: string;
  org?: string | null;
  role?: string | null;
  start?: string | null;
  end?: string | null;
  skills: string[];
  evidence?: string | null;
};

export type Profile = {
  person: {
    name: string;
    email: string;
    phone?: string | null;
    location: string;
    links: Record<string, string>;
    work_authorization?: string | null;
    needs_sponsorship: boolean;
    resume_file?: string | null;
  };
  preferences: {
    target_titles: string[];
    avoid_title_keywords: string[];
    locations: string[];
    remote_ok: boolean;
    min_base_salary_usd?: number | null;
    exclude_companies: string[];
    max_posting_age_days?: number | null;
  };
  answers: {
    work_location?: string | null;
    willing_to_relocate?: boolean | null;
    relocation_targets: string[];
    open_to_remote?: boolean | null;
    earliest_start?: string | null;
    salary_expectation?: string | null;
    how_did_you_hear?: string | null;
    self_identification: "decline" | "ask_me";
    extra: Record<string, string>;
  };
  facts: Fact[];
};

export type ProposedFact = { fact: Fact; source_quote: string; verified: boolean; problems: string[] };
export type ImportResult = {
  contact: { name?: string | null; email?: string | null; phone?: string | null; location?: string | null;
             links: Record<string, string> };
  facts: ProposedFact[];
  suggested_titles: string[];
};
export type RefineResult = {
  refinement: {
    verdict: "good" | "improve";
    why: string;
    suggestions: { text: string; kind: FactKind; skills: string[] }[];
    questions: string[];
  };
  rejected: string[];
};

/** Thrown for 422s, carrying FastAPI's per-field errors keyed like "facts.2.text". */
export class ValidationError extends Error {
  constructor(public fields: Record<string, string>) {
    super("Some fields need fixing");
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  return handle<T>(res);
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    if (res.status === 422 && Array.isArray(body.detail)) {
      const fields: Record<string, string> = {};
      for (const e of body.detail as { loc: (string | number)[]; msg: string }[]) {
        const path = e.loc.slice(1).join(".");
        fields[path] = friendly(path, e.msg.replace(/^Value error, /, ""));
      }
      throw new ValidationError(fields);
    }
    throw new Error(typeof body.detail === "string" ? body.detail : `${res.status} ${res.statusText}`);
  }
  return res.json();
}

/** Turn Pydantic's messages into ones a person can act on. */
function friendly(path: string, msg: string): string {
  if (path.endsWith("email") && msg.includes("pattern")) return "Enter an email address, like you@example.com.";
  if (path.endsWith(".id") && msg.includes("pattern")) return "Ids use lowercase letters, numbers and dashes.";
  if (msg.includes("at least 1 character")) return "Required.";
  if (/at least 10 characters/.test(msg)) return "Write the full claim: at least 10 characters.";
  if (msg.toLowerCase().includes("url")) return "Enter a full link, starting with https://.";
  if (msg.includes("at least 1 item")) return "Add at least one.";
  if (msg.startsWith("duplicate fact id")) return `Two facts share an id (${msg.split(": ")[1]}). Remove one.`;
  return msg;
}

export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  return handle<T>(await fetch(`${API}${path}`, { method: "POST", body: form }));
}

export const control = (runId: string, action: string) =>
  api<Run>(`/api/runs/${runId}/control`, { method: "POST", body: JSON.stringify({ action }) });
