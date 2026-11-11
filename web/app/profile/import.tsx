"use client";

import { useRef, useState } from "react";
import { upload, type ImportResult } from "@/lib/api";
import { KIND_LABEL } from "./facts";

export type Accepted = {
  facts: ImportResult["facts"][number]["fact"][];
  contact: ImportResult["contact"];
  titles: string[];
};

export function ResumeImport({ available, onAccept }: { available: boolean; onAccept: (a: Accepted) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [titles, setTitles] = useState<Set<string>>(new Set());

  async function read(file: File) {
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = await upload<ImportResult>("/api/profile/import", file);
      setResult(r);
      // Verified facts start selected; ones that failed the check start unselected.
      setPicked(new Set(r.facts.flatMap((f, i) => (f.verified ? [i] : []))));
      setTitles(new Set());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }

  function accept() {
    if (!result) return;
    onAccept({ facts: result.facts.filter((_, i) => picked.has(i)).map((f) => f.fact), contact: result.contact,
               titles: [...titles] });
    setResult(null);
  }

  const toggle = (i: number) => setPicked((p) => { const n = new Set(p); n.has(i) ? n.delete(i) : n.add(i); return n; });

  return (
    <section className="panel import" aria-labelledby="import-title">
      <h2 id="import-title">Start from your resume</h2>
      <p className="section-lede">
        Upload a PDF, Word or text resume. Each line becomes a proposed fact, and each one is checked against the
        resume’s own text before you see it. Nothing is added until you choose.
      </p>
      {!available ? (
        <p className="notice">Needs an Anthropic API key in <code>.env</code>. You can still fill everything in below.</p>
      ) : (
        <div className="import-picker">
          <input ref={input} id="resume-file" type="file" accept=".pdf,.docx,.txt,.md" disabled={busy}
                 onChange={(e) => e.target.files?.[0] && read(e.target.files[0])} />
          <label htmlFor="resume-file" className={`button${busy ? " is-busy" : ""}`}>
            {busy ? "Reading your resume…" : "Choose resume file"}
          </label>
          {busy && <span className="muted">This usually takes 20 to 40 seconds.</span>}
        </div>
      )}
      {error && <p className="notice notice-error" role="alert">{error}</p>}

      {result && (
        <div className="import-review">
          <h3>Found {result.facts.length} facts</h3>
          <ul className="proposals">
            {result.facts.map((p, i) => (
              <li key={i} className={p.verified ? "" : "is-unverified"}>
                <label>
                  <input type="checkbox" checked={picked.has(i)} onChange={() => toggle(i)} />
                  <span>
                    <span className="proposal-kind">{KIND_LABEL[p.fact.kind]}{p.fact.org ? `, ${p.fact.org}` : ""}</span>
                    <span className="proposal-text">{p.fact.text}</span>
                    {!p.verified && <span className="proposal-problem">Didn’t match your resume: {p.problems.join(" ")}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>

          {result.suggested_titles.length > 0 && (
            <>
              <h3>Job titles to search for</h3>
              <div className="title-picks">
                {result.suggested_titles.map((t) => (
                  <button key={t} type="button" aria-pressed={titles.has(t)}
                          className={`chip${titles.has(t) ? " is-on" : ""}`}
                          onClick={() => setTitles((s) => { const n = new Set(s); n.has(t) ? n.delete(t) : n.add(t); return n; })}>
                    {t}
                  </button>
                ))}
              </div>
            </>
          )}

          <div className="import-actions">
            <button type="button" className="quiet" onClick={() => setResult(null)}>Discard</button>
            <button type="button" onClick={accept} disabled={picked.size === 0 && titles.size === 0}>
              Add {picked.size} fact{picked.size === 1 ? "" : "s"}{titles.size ? ` and ${titles.size} titles` : ""}
            </button>
          </div>
          <p className="field-hint">Contact details from the resume only fill fields you’ve left empty.</p>
        </div>
      )}
    </section>
  );
}
