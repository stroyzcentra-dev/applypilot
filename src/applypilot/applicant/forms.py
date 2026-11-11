"""Fill the standard fields of an application form from the profile.

Fields are found by their visible label (the same way a person reads the form),
not by site-specific CSS ids, so one mapper covers Greenhouse, Lever and Ashby.
Only facts the user has given are entered. Anything else that's required is
reported back as "needs your answer"; the agent never guesses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from applypilot.live.browser import AgentPage
from applypilot.models import Person

# One pass over the DOM: every visible, editable field with the question a person
# would read for it. Checkbox and radio options are collapsed into one entry per
# question ("What is your location preference?"), not one per option ("NYC").
# Tags each field with data-ap-field so we can target it reliably afterwards.
_SCAN_JS = r"""
() => {
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return (r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none')
      || el.type === 'file';  // file inputs are often hidden behind a styled button
  };
  const text = el => (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
  const STAR = /\*|✱/;
  // Required markers are often drawn with CSS (label::after { content: "*" }), not text.
  const starred = l => !!l && (STAR.test(text(l))
    || STAR.test(getComputedStyle(l, '::after').content) || STAR.test(getComputedStyle(l, '::before').content));
  // The field's own label element and text, if it has one that's really its own.
  const ownLabel = el => {
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (text(l)) return [l, text(l)]; }
    if (el.getAttribute('aria-label')) return [null, el.getAttribute('aria-label')];
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(' ').map(id => text(document.getElementById(id))).join(' ').trim(); if (t) return [null, t]; }
    const wrap = el.closest('label'); if (text(wrap)) return [wrap, text(wrap)];
    return [null, ''];
  };
  // A label that names some other specific control (label[for=...]) isn't a question
  // heading for this field: it's that control's own label, or one option of a group.
  // (Ashby points a question's label at a non-control question id; that still counts.)
  const belongsElsewhere = (l, el) => {
    const f = l.getAttribute('for');
    if (!f || f === el.id) return false;
    const target = document.getElementById(f);
    return !!target && target !== el && target.matches('input, textarea, select');
  };
  // The question above the field: nearest ancestor label/legend that isn't the field's own.
  const isChoice = c => c.type === 'checkbox' || c.type === 'radio';
  const question = el => {
    let n = el.parentElement;
    for (let depth = 0; n && depth < 6; depth++, n = n.parentElement) {
      // Stop once this ancestor also holds a different field: any label found from here
      // up would be that field's question, not ours. (A choice group's sibling options are fine.)
      const others = [...n.querySelectorAll('input, textarea, select')].filter(c =>
        c !== el && c.type !== 'hidden' && !(isChoice(el) && isChoice(c)));
      if (others.length) break;
      for (const l of n.querySelectorAll('legend, label')) {
        if (l.contains(el) || l.querySelector('input, textarea, select') || belongsElsewhere(l, el)) continue;
        if (l.getAttribute('for') === el.id && el.id) continue;
        if (text(l)) return [n, l, text(l)];
      }
    }
    return [null, null, ''];
  };
  const out = [], groups = new Map();
  document.querySelectorAll('input, textarea, select').forEach((el, i) => {
    const type = (el.type || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type) || el.disabled || !visible(el)) return;
    el.dataset.apField = String(i);
    const choice = type === 'checkbox' || type === 'radio';
    const [ownEl, own] = ownLabel(el);
    let label = own, labelEl = ownEl, container = null;
    if (choice || !own) {
      const [c, qEl, q] = question(el);
      if (q) { label = q; labelEl = qEl; container = c; }
    }
    label = (label || el.placeholder || el.name || '').slice(0, 200);
    const required = el.required || el.getAttribute('aria-required') === 'true'
      || STAR.test(label) || starred(labelEl);
    const filled = choice ? el.checked : !!el.value;

    if (choice && container) {
      const opt = { key: String(i), label: own };
      const g = groups.get(container);
      if (g) { g.required ||= required; g.filled ||= filled; g.choices.push(opt); return; }
      const entry = { key: String(i), tag: 'group', type, label, required, filled, choices: [opt],
                      max_length: null, combobox: false };
      groups.set(container, entry);
      out.push(entry);
      return;
    }
    const choices = el.tagName === 'SELECT'
      ? [...el.options].filter(o => o.value !== '' && o.text.trim()).map(o => ({ key: String(i), label: o.text.trim() }))
      : [];
    out.push({
      key: String(i), tag: el.tagName.toLowerCase(), type, label, required, filled, choices,
      max_length: el.maxLength > 0 ? el.maxLength : null,
      // Typeahead boxes need a suggestion picked from a list; typing alone doesn't answer them.
      combobox: el.getAttribute('role') === 'combobox' || !!el.getAttribute('aria-autocomplete')
        || !!el.getAttribute('list'),
    });
  });
  return out;
}
"""


@dataclass
class FormField:
    key: str
    tag: str
    type: str
    label: str
    required: bool
    filled: bool
    choices: list[dict] = field(default_factory=list)  # [{key, label}] for select / group
    max_length: int | None = None
    combobox: bool = False

    @property
    def clean_label(self) -> str:
        return re.sub(r"[*✱]", "", self.label).strip()

    @property
    def options(self) -> int:
        return len(self.choices)

    @property
    def kind(self) -> str:
        """What sort of answer the field takes, in the Writer's terms."""
        if self.type == "file":
            return "file"
        if self.tag == "group":
            return "multi_choice" if self.type == "checkbox" else "single_choice"
        if self.tag == "select":
            return "single_choice"
        if self.combobox:
            return "typeahead"
        return "long_text" if self.tag == "textarea" else "short_text"


@dataclass
class FillResult:
    filled: list[str] = field(default_factory=list)
    needs_input: list[str] = field(default_factory=list)
    skipped_optional: int = 0


def _rules(person: Person) -> list[tuple[re.Pattern, str | None, str]]:
    """(label pattern, value, which profile field it came from). First match wins."""
    links = {k.lower(): str(v) for k, v in person.links.items()}
    r = lambda p: re.compile(p, re.IGNORECASE)  # noqa: E731
    return [
        (r(r"^(legal\s+)?first\s*name"), person.first_name, "person.name"),
        (r(r"^(legal\s+)?last\s*name|^surname"), person.last_name, "person.name"),
        (r(r"^((full|legal)\s+)?name\b(?!.*(company|school|employer|reference))"), person.name,
         "person.name"),
        (r(r"e-?mail"), person.email, "person.email"),
        (r(r"^(phone|mobile|telephone)"), person.phone, "person.phone"),
        (r(r"linkedin"), links.get("linkedin"), "person.links.linkedin"),
        (r(r"github"), links.get("github"), "person.links.github"),
        (r(r"^(website|portfolio|personal (site|website))"), links.get("portfolio"),
         "person.links.portfolio"),
        (r(r"^(current\s+)?(location|city)\b"), person.location, "person.location"),
    ]


_RESUME = re.compile(r"resume|résumé|\bcv\b", re.IGNORECASE)


async def scan(ap: AgentPage) -> list[FormField]:
    return [FormField(**f) for f in await ap.page.evaluate(_SCAN_JS)]


def has_resume_upload(fields: list[FormField]) -> bool:
    return any(f.type == "file" and _RESUME.search(f.label) for f in fields)


async def fill_standard_fields(
    ap: AgentPage, person: Person, tailored_resume: Path | None = None
) -> FillResult:
    """Fill contact fields and the resume upload.

    `tailored_resume`, when given, is uploaded instead of person.resume_file.
    """
    result = FillResult()
    rules = _rules(person)
    fields = await scan(ap)

    for f in fields:
        loc = ap.page.locator(f'[data-ap-field="{f.key}"]')
        label = f.clean_label or "(unlabeled field)"

        if f.type == "file":
            if _RESUME.search(f.label) and tailored_resume and tailored_resume.exists():
                await ap.upload(loc, tailored_resume, label=label,
                                why="Resume tailored for this posting; every bullet restates one "
                                    "of your facts.")
                result.filled.append(label)
            elif _RESUME.search(f.label) and person.resume_file and Path(person.resume_file).exists():
                await ap.upload(loc, Path(person.resume_file), label=label,
                                why="Resume file from person.resume_file in your profile.")
                result.filled.append(label)
            elif f.required:
                result.needs_input.append(label)
            continue

        if f.filled or f.tag in ("select", "group") or f.type in ("checkbox", "radio"):
            # Dropdowns, checkboxes and pre-filled fields are left alone in this pass:
            # they're usually screening questions, which need your answer.
            if f.required and not f.filled:
                result.needs_input.append(label)
            continue

        match = next(((v, src) for pat, v, src in rules if pat.search(f.clean_label)), None)
        if match and match[0]:
            await ap.fill(loc, match[0], label=label, why=f"From {match[1]} in your profile.")
            result.filled.append(label)
        elif f.required:
            result.needs_input.append(label)
        else:
            result.skipped_optional += 1

    return result


async def find_submit(ap: AgentPage):
    """The form's submit button, or None. Never clicked without the user's decision."""
    page = ap.page
    for loc in (
        page.get_by_role("button", name=re.compile(r"^\s*submit( application)?\s*$", re.I)),
        page.locator("button[type=submit], input[type=submit]"),
        page.get_by_role("button", name=re.compile(r"submit|apply", re.I)),
    ):
        if await loc.count():
            return loc.first
    return None


async def required_still_empty(ap: AgentPage) -> list[str]:
    return [f.clean_label for f in await scan(ap) if f.required and not f.filled]
