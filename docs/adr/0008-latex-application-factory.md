# ADR-0008 — LaTeX application factory

- Status: Accepted (Phase 6)
- Date: 2026-11-04

## Context

Phase 6 tailors the user's own LaTeX CV and cover letter to each job and compiles them to PDF.
Templates arrive filled in with the user's details (RenderCV, Overleaf exports), TeX is a full
programming language, the LLM budget is 8,000 tokens a minute and 200,000 a day, and no tailored
document may contain a fact the master profile does not.

## Decision

1. **Engine: TeX Live (Debian, curated) + latexmk** in the offline sandbox: pdfLaTeX, XeLaTeX and
   LuaLaTeX, the common CV packages and fonts, French and English. Tectonic was rejected because it
   fetches packages on demand, which an offline sandbox cannot do. The image is ~3.7 GB, most of it
   `texlive-fonts-extra` (needed by fontawesome5, Source Sans and similar CV fonts).
2. **Template language: Jinja with LaTeX delimiters** (`\VAR{}`, `\BLOCK{}`, `\#{}`), sandboxed,
   strict, with `latex_escape` as `finalize` so escaping cannot be forgotten.
3. **Conversion = proposal + proof.** The converter splits the `.tex` into preamble, header and
   sections. Small parts get exact find/replace edits (never a retyped preamble); sections are
   rewritten with loops. For each part the model also returns the data it saw, and the part is
   accepted only if rendering it with that data gives back the original's visible words
   (≥ 97 %, and no number or date missing). Failed parts stay verbatim. Two slips the model makes
   (`\VAR{x}|filter`, literal `\n`) are repaired deterministically. The user then reviews the
   check, the headings (EN/FR) and a sandboxed preview and confirms; any later edit needs a new
   confirmation.
4. **The round-trip data rebuilds the master profile** on request (ids carried over by
   similarity), which fixed a Phase 4 parser defect (duplicated sections, dropped bold words).
5. **Tailoring returns a plan, not a document** (ids + rewritten bullets with their source ids);
   `validate_plan` repairs it and `build_tailored` assembles facts from the master. Page budget:
   compile, and while over budget drop the plan's least useful bullet and recompile (≤ 8 rounds).
6. **Versioning**: each generation adds a `generated_document` version per kind on a draft
   `application` (one per job); files live under `storage/documents/<application>/`.
7. **ATS before/after** is the job-keyword coverage of the master profile vs. the text read back
   from the generated PDF.

## Consequences

- Converting one CV costs ~10–15 LLM calls (~40–60 k tokens) once; generating one application
  costs 2 calls. A full day of experiments can exhaust Groq's daily budget.
- The guardrail is conservative: a correct rewrite using a word the profile lacks is reverted.
  That costs some tailoring quality, never truth.
- Templates whose structure the converter cannot loop over stay verbatim for that part; the user
  can edit the Jinja source directly.
