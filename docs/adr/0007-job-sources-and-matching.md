# ADR-0007 — Job sources, crawling etiquette and local matching

- Status: Accepted (Phase 5)
- Date: 2026-11-04

## Context

Phase 5 aggregates postings from several boards and ranks them against the master profile. The
sources differ in what they allow, postings are untrusted text, and the host has ~3.8 GB of
Docker memory and no GPU.

## Decision

1. **Sources, by what each one permits** (checked on 2026-11-04):
   - official APIs: Remotive and Remote OK (link back + name the source, Remotive ≤ 4 calls a day),
     Adzuna and JSearch (personal keys, free tiers);
   - public RSS: We Work Remotely, Tanitjobs (terms allow personal, non-commercial use);
   - public listing pages: Keejob (allowed by its `robots.txt`; terms silent on automation;
     approved by the user), a few pages twice a day, 6 s apart;
   - excluded: OptionCarriere (`robots.txt` forbids job pages and RSS), LinkedIn and Indeed
     (brief), Emploi.tn (unreachable).
2. **One polite HTTP client** for all connectors: identifiable user agent with a contact URL,
   `robots.txt` checked with Protego (Google's wildcard / longest-match rules) before any scraped
   page and re-read daily, per-source daily caps and per-host pacing in Redis (shared by every
   process), a 3-hour response cache, retries honouring `Retry-After`, an 8 MB response cap, and
   `defusedxml` for feeds. A source that starts disallowing us stops by itself.
3. **Normalization is deterministic** (location → country/region, work mode, language, domain,
   seniority, contract) and conservative: unknown stays unknown. Duplicates across boards share a
   `dedup_hash` (normalized title + company + place) and are linked (`also_on`), not stored twice.
   Only `http(s)` links and well-formed e-mails are kept from postings.
4. **Local embeddings**: `intfloat/multilingual-e5-small` through ONNX Runtime (int8 export,
   384-d) instead of PyTorch: ~250 MB in memory instead of ~1.5 GB, FR/EN/AR, no API cost, no
   text leaves the machine. The files are fetched from a pinned Hugging Face revision and refused
   unless their SHA-256 matches.
5. **Matching in three stages**: cosine similarity (pgvector) and keyword overlap give every job a
   pre-score; the best `MATCH_RERANK_TOP_N` (10) unreviewed jobs then go to the JobMatcherAgent,
   a tool-less structured-output call (stricter than the read-only tools the brief allows), in
   batches of 5. Before that, each of them is checked by the guard model; flagged postings are
   never sent to the LLM.

## Consequences

- Coverage without keys: remote boards worldwide plus two Tunisian boards; Adzuna/JSearch add
  Europe, North America and the Gulf once their keys are set.
- The Groq cost of a run is bounded: ≤ 2 re-rank calls (10 jobs) twice a day, plus on-demand
  reviews from the job panel.
- Remotive's public API currently returns only its latest ~10 postings (its full catalogue is a
  paid API).
- A layout change on Keejob shows up as `parse_empty` on the Sources panel instead of failing
  silently.
