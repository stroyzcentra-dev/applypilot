"use client";

import { useEffect, useMemo, useState } from "react";
import { api, ValidationError, type Fact, type FactKind, type Profile } from "@/lib/api";
import { FactEditor, KIND_LABEL, newFactId } from "./facts";
import { NumberField, PairsEditor, TagInput, TextField, Toggle, TriState } from "./fields";
import { ResumeImport, type Accepted } from "./import";

export default function ProfilePage() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [saved, setSaved] = useState<string>("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [aiAvailable, setAiAvailable] = useState(false);

  useEffect(() => {
    api<Profile>("/api/profile")
      .then((p) => { setProfile(p); setSaved(JSON.stringify(p)); })
      .catch((e) => setLoadError(`Can't reach ApplyPilot. Start it first. (${e.message})`));
    api<{ api_key_set: boolean }>("/api/status").then((s) => setAiAvailable(s.api_key_set)).catch(() => {});
  }, []);

  const dirty = profile !== null && JSON.stringify(profile) !== saved;

  // Warn before leaving with unsaved edits.
  useEffect(() => {
    if (!dirty) return;
    const h = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirty]);

  const ids = useMemo(() => new Set(profile?.facts.map((f) => f.id) ?? []), [profile]);

  if (loadError) return <p className="notice notice-error" role="alert">{loadError}</p>;
  if (!profile) return <p className="muted">Loading your profile…</p>;

  const p = profile;
  const isExample = p.person.email.endsWith("@example.com");
  const update = (fn: (draft: Profile) => void) => {
    const next = structuredClone(p);
    fn(next);
    setProfile(next);
    setMessage(null);
  };
  const e = (path: string) => errors[path];

  async function save() {
    setSaving(true);
    setErrors({});
    setMessage(null);
    try {
      const out = await api<Profile>("/api/profile", { method: "PUT", body: JSON.stringify(p) });
      setProfile(out);
      setSaved(JSON.stringify(out));
      setMessage("Saved. The next application uses these facts.");
    } catch (err) {
      if (err instanceof ValidationError) {
        setErrors(err.fields);
        setMessage(`Fix ${Object.keys(err.fields).length} field${Object.keys(err.fields).length > 1 ? "s" : ""} marked below, then save.`);
      } else {
        setMessage((err as Error).message);
      }
    } finally {
      setSaving(false);
    }
  }

  function addFact(kind: FactKind, base: Partial<Fact> = {}) {
    update((d) => {
      const taken = new Set(d.facts.map((f) => f.id));
      d.facts.push({ kind, text: "", skills: [], ...base, id: newFactId(kind, base.org ?? base.text ?? "new", taken) });
    });
  }

  function acceptImport(a: Accepted) {
    update((d) => {
      const taken = new Set(d.facts.map((f) => f.id));
      for (const f of a.facts) {
        d.facts.push({ ...f, id: taken.has(f.id) ? newFactId(f.kind, f.org ?? f.text, taken) : f.id });
        taken.add(f.id);
      }
      // On the example profile, the resume's contact details replace the placeholders.
      // On a real profile they only fill fields you've left empty.
      const c = a.contact, person = d.person;
      if (c.name && (isExample || !person.name)) person.name = c.name;
      if (c.email && (isExample || !person.email)) person.email = c.email;
      if (c.phone && (isExample || !person.phone)) person.phone = c.phone;
      if (c.location && (isExample || !person.location)) person.location = c.location;
      if (isExample) person.links = {};
      for (const [k, v] of Object.entries(c.links)) if (!person.links[k]) person.links[k] = v;
      if (isExample) d.facts = d.facts.filter((f) => !EXAMPLE_FACTS.has(f.id) || !a.facts.length);
      if (isExample && a.titles.length) d.preferences.target_titles = [];
      for (const t of a.titles) {
        if (!d.preferences.target_titles.some((x) => x.toLowerCase() === t.toLowerCase())) {
          d.preferences.target_titles.push(t.toLowerCase());
        }
      }
    });
    setMessage(`Added ${a.facts.length} facts. Review them below, then save.`);
  }

  return (
    <div className="profile">
      <div className="profile-head">
        <h1>Your profile</h1>
        <p className="lede">
          Everything the agent says about you comes from here. Drafted answers and resume bullets cite these facts,
          and anything not here is left for you to answer.
        </p>
        {isExample && (
          <p className="notice notice-caution">
            This is still the example profile for “Jordan Example”. Import your resume or replace it below.
          </p>
        )}
      </div>

      <ResumeImport available={aiAvailable} onAccept={acceptImport} />

      <section className="panel" aria-labelledby="contact-title">
        <h2 id="contact-title">Contact</h2>
        <div className="grid">
          <TextField label="Full name" value={p.person.name} error={e("person.name")}
                     onChange={(v) => update((d) => { d.person.name = v ?? ""; })} />
          <TextField label="Email" type="email" value={p.person.email} error={e("person.email")}
                     onChange={(v) => update((d) => { d.person.email = v ?? ""; })} />
          <TextField label="Phone" type="tel" value={p.person.phone} onChange={(v) => update((d) => { d.person.phone = v; })} />
          <TextField label="Location" value={p.person.location} error={e("person.location")} placeholder="Raleigh, NC, USA"
                     onChange={(v) => update((d) => { d.person.location = v ?? ""; })} />
          <TextField label="Work authorization" value={p.person.work_authorization} placeholder="US citizen"
                     onChange={(v) => update((d) => { d.person.work_authorization = v; })} />
          <TextField label="Resume file (fallback)" value={p.person.resume_file}
                     hint="Used only when a tailored resume can't be made." placeholder="C:\path\to\resume.pdf"
                     onChange={(v) => update((d) => { d.person.resume_file = v; })} />
        </div>
        <Toggle label="I need visa sponsorship" checked={p.person.needs_sponsorship}
                onChange={(v) => update((d) => { d.person.needs_sponsorship = v; })} />
        <PairsEditor label="Links" keyLabel="Name (e.g. github)" valueLabel="https://…" pairs={p.person.links}
                     errors={Object.fromEntries(Object.entries(errors).filter(([k]) => k.startsWith("person.links."))
                       .map(([k, v]) => [k.split(".")[2], v]))}
                     onChange={(links) => update((d) => { d.person.links = links; })} />
      </section>

      <section className="panel" aria-labelledby="prefs-title">
        <h2 id="prefs-title">What you’re looking for</h2>
        <p className="section-lede">Jobs that fail these are skipped before any AI scoring, with the reason logged.</p>
        <TagInput label="Job titles" values={p.preferences.target_titles} error={e("preferences.target_titles")}
                  hint="Every word must appear in the posting’s title, in any order."
                  onChange={(v) => update((d) => { d.preferences.target_titles = v; })} />
        <TagInput label="Skip titles containing" values={p.preferences.avoid_title_keywords} placeholder="staff, manager…"
                  onChange={(v) => update((d) => { d.preferences.avoid_title_keywords = v; })} />
        <TagInput label="Locations" values={p.preferences.locations} placeholder="Raleigh, North Carolina…"
                  onChange={(v) => update((d) => { d.preferences.locations = v; })} />
        <Toggle label="Remote jobs are fine" checked={p.preferences.remote_ok}
                onChange={(v) => update((d) => { d.preferences.remote_ok = v; })} />
        <div className="grid">
          <NumberField label="Minimum base salary (USD)" value={p.preferences.min_base_salary_usd}
                       hint="Only rejects postings that state a lower maximum."
                       onChange={(v) => update((d) => { d.preferences.min_base_salary_usd = v; })} />
          <NumberField label="Skip postings older than (days)" value={p.preferences.max_posting_age_days}
                       onChange={(v) => update((d) => { d.preferences.max_posting_age_days = v; })} />
        </div>
        <TagInput label="Companies to skip" values={p.preferences.exclude_companies}
                  onChange={(v) => update((d) => { d.preferences.exclude_companies = v; })} />
      </section>

      <section className="panel" aria-labelledby="answers-title">
        <h2 id="answers-title">Common questions</h2>
        <p className="section-lede">The agent answers these questions only from here. Leave one empty and it’s left for you.</p>
        <div className="grid">
          <TextField label="Where you’d work from" value={p.answers.work_location}
                     onChange={(v) => update((d) => { d.answers.work_location = v; })} />
          <TextField label="Earliest start" value={p.answers.earliest_start} placeholder="Two weeks after an offer"
                     onChange={(v) => update((d) => { d.answers.earliest_start = v; })} />
          <TriState label="Willing to relocate?" value={p.answers.willing_to_relocate}
                    onChange={(v) => update((d) => { d.answers.willing_to_relocate = v; })} />
          <TriState label="Open to remote?" value={p.answers.open_to_remote}
                    onChange={(v) => update((d) => { d.answers.open_to_remote = v; })} />
          <TextField label="Salary expectation" value={p.answers.salary_expectation}
                     hint="Empty means salary questions are always left for you."
                     onChange={(v) => update((d) => { d.answers.salary_expectation = v; })} />
          <TextField label="How you heard about jobs" value={p.answers.how_did_you_hear} placeholder="Company careers page"
                     onChange={(v) => update((d) => { d.answers.how_did_you_hear = v; })} />
        </div>
        <TagInput label="Places you’d relocate to" values={p.answers.relocation_targets}
                  onChange={(v) => update((d) => { d.answers.relocation_targets = v; })} />
        <fieldset className="radio-group">
          <legend>Voluntary demographic questions (gender, race, veteran, disability)</legend>
          <label><input type="radio" name="selfid" checked={p.answers.self_identification === "ask_me"}
                        onChange={() => update((d) => { d.answers.self_identification = "ask_me"; })} /> Leave them for me</label>
          <label><input type="radio" name="selfid" checked={p.answers.self_identification === "decline"}
                        onChange={() => update((d) => { d.answers.self_identification = "decline"; })} /> Choose “decline to answer”</label>
        </fieldset>
        <PairsEditor label="Other answers you give often" keyLabel="Question (e.g. notice_period)" valueLabel="Answer"
                     pairs={p.answers.extra} onChange={(extra) => update((d) => { d.answers.extra = extra; })} />
      </section>

      <section className="panel" aria-labelledby="facts-title">
        <h2 id="facts-title">Facts</h2>
        <p className="section-lede">
          One specific, checkable claim each. The agent can repeat these to employers but can never go beyond them.
        </p>
        {e("facts") && <p className="field-error">{e("facts")}</p>}
        <ol className="facts">
          {p.facts.map((f, i) => (
            <FactEditor key={f.id + i} fact={f} index={i} errors={errors} refineAvailable={aiAvailable}
                        onChange={(nf) => update((d) => { d.facts[i] = nf; })}
                        onRemove={() => update((d) => { d.facts.splice(i, 1); })}
                        onAdd={(nf) => addFact(nf.kind, nf)} />
          ))}
        </ol>
        <div className="add-fact">
          <span className="muted">Add:</span>
          {(Object.keys(KIND_LABEL) as FactKind[]).map((k) => (
            <button key={k} type="button" className="quiet small" onClick={() => addFact(k)}>{KIND_LABEL[k]}</button>
          ))}
        </div>
        {ids.size !== p.facts.length && <p className="field-error">Two facts share an id; remove one.</p>}
      </section>

      <div className={`savebar${dirty || message ? " is-visible" : ""}`} role="region" aria-label="Save changes">
        <span aria-live="polite">{message ?? (dirty ? "You have unsaved changes." : "")}</span>
        <div>
          {dirty && (
            <button type="button" className="quiet" onClick={() => { setProfile(JSON.parse(saved)); setErrors({}); setMessage(null); }}>
              Discard
            </button>
          )}
          <button type="button" onClick={save} disabled={!dirty || saving}>{saving ? "Saving…" : "Save profile"}</button>
        </div>
      </div>
    </div>
  );
}

// Ids in examples/profile.example.yaml, cleared when a real resume replaces the example.
const EXAMPLE_FACTS = new Set(["exp-acme-api", "exp-acme-latency", "proj-applypilot", "edu-ncsu", "skill-ml"]);
