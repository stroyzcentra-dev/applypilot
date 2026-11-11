"use client";

import { useState } from "react";
import { api, type Fact, type FactKind, type RefineResult } from "@/lib/api";
import { TagInput, TextField } from "./fields";

export const KIND_LABEL: Record<FactKind, string> = {
  experience: "Job",
  achievement: "Achievement in a job",
  project: "Project",
  education: "Education",
  skill: "Skill",
  other: "Other",
};

const PREFIX: Record<FactKind, string> = {
  experience: "exp", achievement: "ach", project: "proj", education: "edu", skill: "skill", other: "fact",
};

/** Readable, unique fact ids: these show up as citations next to drafted text. */
export function newFactId(kind: FactKind, label: string, taken: Set<string>): string {
  const words = (label.toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((w) => w.length > 2).slice(0, 3);
  const base = [PREFIX[kind], ...words].join("-");
  let id = base;
  for (let n = 2; taken.has(id); n++) id = `${base}-${n}`;
  return id;
}

export function FactEditor({
  fact, index, errors, onChange, onRemove, onAdd, refineAvailable,
}: {
  fact: Fact; index: number; errors: Record<string, string>;
  onChange: (f: Fact) => void; onRemove: () => void; onAdd: (f: Omit<Fact, "id">) => void;
  refineAvailable: boolean;
}) {
  const [refining, setRefining] = useState(false);
  const [result, setResult] = useState<RefineResult | null>(null);
  const [refineError, setRefineError] = useState<string | null>(null);
  const err = (k: string) => errors[`facts.${index}.${k}`];
  const set = (patch: Partial<Fact>) => onChange({ ...fact, ...patch });
  const showsJob = fact.kind === "experience" || fact.kind === "achievement" || fact.kind === "education";

  async function refine() {
    setRefining(true);
    setRefineError(null);
    try {
      setResult(await api<RefineResult>("/api/profile/refine", { method: "POST", body: JSON.stringify(fact) }));
    } catch (e) {
      setRefineError((e as Error).message);
    } finally {
      setRefining(false);
    }
  }

  return (
    <li className="fact">
      <div className="fact-head">
        <select aria-label="Kind of fact" value={fact.kind} onChange={(e) => set({ kind: e.target.value as FactKind })}>
          {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
        <code className="fact-id" title="Drafts cite this id">{fact.id}</code>
        <div className="fact-actions">
          {refineAvailable && (
            <button type="button" className="quiet small" onClick={refine} disabled={refining || fact.text.length < 10}>
              {refining ? "Thinking…" : "Refine with AI"}
            </button>
          )}
          <button type="button" className="quiet small" onClick={onRemove}>Remove</button>
        </div>
      </div>

      <label className="sr-only" htmlFor={`fact-${fact.id}`}>Fact text</label>
      <textarea
        id={`fact-${fact.id}`} rows={2} value={fact.text} className={err("text") ? "invalid" : ""}
        placeholder="One specific claim. Numbers beat adjectives: “Cut p95 latency from 900ms to 180ms”."
        onChange={(e) => set({ text: e.target.value })}
      />
      {err("text") && <p className="field-error">{err("text")}</p>}

      <div className="fact-meta">
        {showsJob && <TextField label={fact.kind === "education" ? "School" : "Company"} value={fact.org}
                                onChange={(v) => set({ org: v })} />}
        {fact.kind === "experience" && <TextField label="Title" value={fact.role} onChange={(v) => set({ role: v })} />}
        {(fact.kind === "experience" || fact.kind === "education") && (
          <>
            <TextField label="Start" placeholder="2024-06" value={fact.start} onChange={(v) => set({ start: v })} />
            <TextField label="End" placeholder="present" value={fact.end} onChange={(v) => set({ end: v })} />
          </>
        )}
        {fact.kind === "project" && (
          <TextField label="Link" type="url" value={fact.evidence} error={err("evidence")}
                     onChange={(v) => set({ evidence: v })} />
        )}
      </div>
      <TagInput label="Skills it shows" values={fact.skills} onChange={(skills) => set({ skills })}
                placeholder="Python, FastAPI…" />

      {refineError && <p className="notice notice-error">{refineError}</p>}
      {result && (
        <div className="refine">
          <p className="refine-why">
            <strong>{result.refinement.verdict === "good" ? "This one’s solid." : "Could be sharper."}</strong>{" "}
            {result.refinement.why}
          </p>
          {result.refinement.suggestions.length > 0 && (
            <ul className="refine-list">
              {result.refinement.suggestions.map((s, i) => (
                <li key={i}>
                  <span>{s.text}</span>
                  <span className="refine-buttons">
                    <button type="button" className="small"
                            onClick={() => { set({ text: s.text, skills: s.skills.length ? s.skills : fact.skills }); setResult(null); }}>
                      Use this
                    </button>
                    <button type="button" className="quiet small"
                            onClick={() => onAdd({ kind: s.kind, text: s.text, skills: s.skills, org: fact.org, role: null })}>
                      Add as a new fact
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          )}
          {result.refinement.questions.length > 0 && (
            <>
              <p className="refine-q-lede">Answer these in the fact above to make it stronger:</p>
              <ul className="refine-questions">
                {result.refinement.questions.map((q) => <li key={q}>{q}</li>)}
              </ul>
            </>
          )}
          {result.rejected.length > 0 && (
            <p className="field-hint">
              Hid {result.rejected.length} suggestion{result.rejected.length > 1 ? "s" : ""} that added numbers your fact
              doesn’t have.
            </p>
          )}
          <button type="button" className="quiet small" onClick={() => setResult(null)}>Dismiss</button>
        </div>
      )}
    </li>
  );
}
