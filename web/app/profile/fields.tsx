"use client";

import { useId, useState } from "react";

export function Field({
  label, hint, error, children,
}: { label: string; hint?: string; error?: string; children: (id: string) => React.ReactNode }) {
  const id = useId();
  return (
    <div className={`field${error ? " has-error" : ""}`}>
      <label htmlFor={id}>{label}</label>
      {children(id)}
      {error ? <p className="field-error" role="alert">{error}</p> : hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}

/** Text input bound to a nullable string: empty means "not set" (null). */
export function TextField({
  label, value, onChange, hint, error, placeholder, type = "text",
}: {
  label: string; value: string | null | undefined; onChange: (v: string | null) => void;
  hint?: string; error?: string; placeholder?: string; type?: string;
}) {
  return (
    <Field label={label} hint={hint} error={error}>
      {(id) => (
        <input id={id} type={type} value={value ?? ""} placeholder={placeholder}
               onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)} />
      )}
    </Field>
  );
}

export function NumberField({
  label, value, onChange, hint, error,
}: { label: string; value: number | null | undefined; onChange: (v: number | null) => void;
     hint?: string; error?: string }) {
  return (
    <Field label={label} hint={hint} error={error}>
      {(id) => (
        <input id={id} type="number" inputMode="numeric" value={value ?? ""}
               onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} />
      )}
    </Field>
  );
}

/** Yes / No / Not set, for optional booleans. */
export function TriState({
  label, value, onChange, hint,
}: { label: string; value: boolean | null | undefined; onChange: (v: boolean | null) => void; hint?: string }) {
  return (
    <Field label={label} hint={hint}>
      {(id) => (
        <select id={id} value={value == null ? "" : value ? "yes" : "no"}
                onChange={(e) => onChange(e.target.value === "" ? null : e.target.value === "yes")}>
          <option value="">Not set: leave it for me</option>
          <option value="yes">Yes</option>
          <option value="no">No</option>
        </select>
      )}
    </Field>
  );
}

export function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

/** A list of short strings. Enter or comma adds; each chip can be removed. */
export function TagInput({
  label, values, onChange, hint, error, placeholder,
}: { label: string; values: string[]; onChange: (v: string[]) => void; hint?: string; error?: string;
     placeholder?: string }) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const items = raw.split(",").map((s) => s.trim()).filter(Boolean);
    const next = [...values];
    for (const item of items) if (!next.some((v) => v.toLowerCase() === item.toLowerCase())) next.push(item);
    onChange(next);
    setDraft("");
  };
  return (
    <Field label={label} hint={hint} error={error}>
      {(id) => (
        <div className="tags">
          {values.map((v) => (
            <span key={v} className="tag">
              {v}
              <button type="button" aria-label={`Remove ${v}`} onClick={() => onChange(values.filter((x) => x !== v))}>
                ×
              </button>
            </span>
          ))}
          <input
            id={id} value={draft} placeholder={placeholder ?? "Type and press Enter"}
            onChange={(e) => (e.target.value.endsWith(",") ? add(e.target.value) : setDraft(e.target.value))}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); add(draft); }
              if (e.key === "Backspace" && !draft && values.length) onChange(values.slice(0, -1));
            }}
            onBlur={() => draft && add(draft)}
          />
        </div>
      )}
    </Field>
  );
}

/** Editable key/value rows (links, extra answers). */
export function PairsEditor({
  label, pairs, onChange, keyLabel, valueLabel, hint, errors,
}: {
  label: string; pairs: Record<string, string>; onChange: (p: Record<string, string>) => void;
  keyLabel: string; valueLabel: string; hint?: string; errors?: Record<string, string>;
}) {
  const rows = Object.entries(pairs);
  const set = (i: number, k: string, v: string) => {
    const next = rows.map((r, j) => (j === i ? [k, v] : r));
    onChange(Object.fromEntries(next));
  };
  return (
    <fieldset className="pairs">
      <legend>{label}</legend>
      {hint && <p className="field-hint">{hint}</p>}
      {rows.map(([k, v], i) => (
        <div key={i} className="pair">
          <input aria-label={keyLabel} placeholder={keyLabel} value={k} onChange={(e) => set(i, e.target.value, v)} />
          <input aria-label={valueLabel} placeholder={valueLabel} value={v} onChange={(e) => set(i, k, e.target.value)} />
          <button type="button" className="quiet small" onClick={() => onChange(Object.fromEntries(rows.filter((_, j) => j !== i)))}>
            Remove
          </button>
          {errors?.[k] && <p className="field-error">{errors[k]}</p>}
        </div>
      ))}
      <button type="button" className="quiet small"
              onClick={() => onChange({ ...pairs, [rows.some(([k]) => k === "") ? `new-${rows.length}` : ""]: "" })}>
        Add
      </button>
    </fieldset>
  );
}
