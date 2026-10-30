# ADR-0006 — CV handling and the master profile

- Status: Accepted (Phase 4)
- Date: 2026-10-31

## Context

Phase 4 accepts an uploaded CV (PDF or DOCX), turns it into a structured profile and grades it for
applicant tracking systems (ATS). The CV is untrusted input: a template downloaded from the web, or
a file edited by someone else, can carry hidden text, active content or malformed structures. The
profile then becomes the only source of facts the Phase 6 tailoring agents may use.

## Decision

1. **Validation by content.** The API accepts a file only if its bytes are a PDF (`%PDF-` header,
   EOF marker) or a DOCX (OOXML zip with the `wordprocessingml.document.main` part, no VBA project,
   bounded entries, uncompressed size and compression ratio). The name and declared type are
   ignored. Files are stored as `<storage_dir>/resumes/<128-bit random>.<ext>`, mode 0600, outside
   any web root, and served back only as attachments (`nosniff`, `CSP: sandbox`).
2. **Parsing happens in the worker**, never in the API process: PyMuPDF / python-docx open the
   file with a timeout, a page cap and a scan of every PDF object for JavaScript, launch actions
   and embedded files (such a file is refused and deleted).
3. **Invisible text is separated, not parsed.** White-on-white (unless over a dark fill or image),
   tiny, transparent and off-page PDF text, and Word `vanish` / white / tiny runs, are kept apart:
   they are scanned for prompt injection, shown to the user and reported as an ATS issue (keyword
   stuffing), but never sent to the parser.
4. **ResumeParserAgent has no tools.** It is a single structured-output call (JSON schema, one
   corrective retry) on the spotlighted visible text. It must copy values verbatim; a grounding
   check then flags every value whose words are not in the document, and the profile form shows
   those values for review.
5. **Stable ids.** Every experience, education entry, project, bullet, skill group, language,
   certification, link and extra section gets an id when first saved; ids survive edits and
   reordering. Phase 6 tailoring must reference these ids, so a generated bullet without a source
   id can be rejected mechanically.
6. **The master profile changes only on an explicit save.** A first upload is shown as a draft; a
   later upload is compared section by section (keep / add new items / replace) and nothing is
   applied until the user confirms.
7. **ATS rules are deterministic**; the LLM review only adds content feedback and a target job's
   keywords. Suggested rewrites cannot introduce numbers: any number not in the original bullet is
   replaced by a `[N]` placeholder.

## Consequences

- An injection hidden in a CV is visible to the user and cannot reach the parser; one in the
  visible text can at worst produce a wrong field in a draft that the user reviews before saving.
- PyMuPDF is AGPL-3.0: fine for this local, single-user tool; a hosted multi-user deployment
  would need a commercial licence or another PDF library.
- Scanned (image-only) CVs are not OCR'd: they get a critical ATS issue, since real ATS cannot
  read them either.
