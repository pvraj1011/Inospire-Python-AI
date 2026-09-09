# Task Brief: Legal Case-Document Entity Extraction (Internship Assessment)

You are working inside an Antigravity project folder. The file `vraj.pdf` sits alongside
this brief — it is a real 87-page Patna High Court judgment (a batched writ-petition
matter). Your job is to build a working extraction pipeline, run it against this PDF,
and produce the deliverables listed below within this session. Do not ask the user to
manually confirm each design choice — make the documented default decision, implement
it, and note the decision in `approach.md`. Only stop and ask if you hit something this
brief genuinely does not cover.

## 1. What is actually being asked

The recruiter's email asks for five extracted fields from one court case document:
`court_name`, `court_bench`, `judge_name`, `case_number` + `case_type`, and the
`act`/`section` combination that the case concerns. Missing entities become `null`
(never omit a key, never leave a field out). The result is returned as a Python
dictionary, dumped to a JSON file, and accompanied by a short written explanation of
method and time taken. Treat this as a mini document-processing system, not a one-off
manual answer — the wording ("each one should pick up only 1 case", "based on the
approach, we shall proceed with next steps") reads like they are evaluating whether you
can build something that would generalize to the rest of their document set, not just
whether you can eyeball one PDF correctly.

## 2. Recon already done on `vraj.pdf` — use this, don't rediscover it

This document has already been read end-to-end. Build the extraction logic around
these confirmed facts instead of guessing from page 1 alone:

- **It's a batched judgment.** The front pages list ten different tagged case numbers
  ("Civil Writ Jurisdiction Case No. 16760 of 2023 ... with ... No. 16882 of 2023 ...
  with ... No. 17494 of 2023 ..." and so on). A naive "grab the first case-number regex
  match" approach is fine here because the lead case happens to be first, but the
  robust signal is the running header/footer that repeats on **every single page**:
  `Patna High Court CWJC No.16760 of 2023 dt.20-06-2024`. Anchor `case_number` and
  `case_type` extraction to that repeated footer line, not to the front-page caption
  block — the footer is what survives across arbitrary court-document formats.
- **The Chief Justice's full name is not in the CORAM line.** The coram at the top
  reads only `CORAM: HONOURABLE THE CHIEF JUSTICE and HONOURABLE MR. JUSTICE HARISH
  KUMAR` — no personal name for the CJ. The full name appears only in the signature
  block on the last page: `(K. Vinod Chandran, CJ)` and `(Harish Kumar, J)`. Any
  judge-name extraction that only looks at the CORAM/header block will silently miss
  the Chief Justice's actual name. Check both the coram line *and* the closing
  signature block, and merge them.
- **"Bench" is a trap word.** The word "Bench" appears dozens of times in this
  document, almost entirely inside citations of *other* Supreme Court cases ("a 9 Judge
  Constitution Bench in Indra Sawhney...", "the Constitution Bench held...", "a
  two-judge bench, in K.C Vasanth Kumar..."). None of these describe the Patna High
  Court bench actually hearing this matter — they're case-law history. Don't regex
  "bench" broadly; see §4 for how to populate `court_bench` instead.
- **There is no `Section N` citation anywhere in the document.** A full-text search
  for the pattern "Section <number>" returns zero matches across all 87 pages. This is
  a constitutional writ petition about reservation policy — it argues from
  Constitution *Articles* (14, 15, 16, 16(4-A), 243D, 243T, 141), not from numbered
  sections of a statute. Do not force an Article number into the `section` field —
  they are a different kind of citation. Expect `section` to legitimately be `null`
  for this document; that is the correct answer here, not a failure of the script.
- **Many Act names in the body are precedent history, not the subject of this case.**
  Regexing for `"... Act, <year>"` across the whole text also pulls in acts mentioned
  only while discussing *other* judgments' backgrounds (e.g. "Constitution (1st
  Amendment) Act 1951", "Backward Commission Act, 1993", "Eighty-first Amendment) Act,
  2000"). The Acts actually under challenge in *this* case are named explicitly in the
  operative/final-order paragraph, which is the reliable place to source `act` from:
  > "We, hence, set aside the Bihar Reservation of Vacancies in Posts and Services (for
  > Scheduled Caste, Scheduled Tribes and Other Backward Classes) Amendment Act, 2023
  > and the Bihar Reservation (in Admission to Educational Institutions) Amendment
  > Act, 2023 as ultra vires the Constitution..."

  That final-order paragraph (search for "set aside", "ultra vires", "quashed", "is
  allowed" near the end of the text) is your primary source for `act`, not a blanket
  scan of every "Act, YYYY" mention.

## 3. Output contract

Produce a flat Python dict with exactly these keys, always present, `null` when not
found:

```python
{
    "court_name": "High Court of Judicature at Patna",
    "court_bench": "Patna",
    "judge_name": ["K. Vinod Chandran, CJ", "Harish Kumar, J"],
    "case_number": "16760 of 2023",
    "case_type": "Civil Writ Jurisdiction Case",
    "act": [
        "Bihar Reservation of Vacancies in Posts and Services (for Scheduled Castes, Scheduled Tribes and Other Backward Classes) Amendment Act, 2023",
        "Bihar Reservation (in Admission to Educational Institutions) Amendment Act, 2023"
    ],
    "section": null
}
```

This is the actual grounded output for `vraj.pdf`, not a placeholder — use it to sanity-check
your script's result, and flag it in `approach.md` if your implementation lands
somewhere meaningfully different so it's a documented judgment call rather than a silent
discrepancy.

Field-shape decisions, and why:

- `judge_name` and `act` are lists. Indian court judgments routinely have more than one
  judge on the bench and more than one Act under challenge; a plain string field would
  force you to either concatenate (lossy) or arbitrarily pick one (wrong). Use a list
  even when only one value is found — keep the shape consistent across documents.
- `court_bench` = the physical seat of the court (here, "Patna" — this High Court has
  only one seat, unlike, say, Bombay or Allahabad which have circuit benches, so the
  value is trivially the same city as `court_name`). This is the standard convention
  used by Indian legal databases (eCourts, Indian Kanoon) when they carry both a
  `court_name` and `court_bench` field. The alternative reading — "bench" meaning
  Single/Division/Full Bench composition — is defensible too; if you go that route
  instead (e.g. `"Division Bench"`, inferred from two names in CORAM), that's fine, but
  say explicitly in `approach.md` which of the two interpretations you picked and why,
  since this is the one field in the schema that's genuinely ambiguous by name alone.
- `case_number` is the number only ("16760 of 2023"); `case_type` is the label
  ("Civil Writ Jurisdiction Case"). Keep them split, matching the email's separate
  numbered steps for each.
- Never drop a key. If an entity truly isn't findable, keep the key with `null` (or
  `[]`→`null` for list fields when nothing was found — don't return an empty list).

## 4. Three ways to build the extractor — pick one, or combine them

**Option A — Rule-based (regex + positional heuristics on the text layer).**
Fast, free, fully deterministic, and every match is traceable to an exact line — which
matters a lot for the "explain your approach" part of the submission. Weakness: every
new court's document formatting needs its own patterns; it's brittle if this pipeline
ever needs to run on documents from a different High Court or a criminal (rather than
civil-writ) matter with different phrasing.

**Option B — General-purpose NLP NER (e.g. spaCy `en_core_web_sm`/`lg`).**
Can pick out `PERSON` and `ORG`/`GPE` spans without hand-written patterns, which helps
with judge names and court names in noisier documents. Weakness: off-the-shelf English
NER has no notion of "case_number", "case_type", or "act+section combination" as
categories — you'd still need regex on top for those, and a generic model isn't tuned
for Indian legal phrasing, so precision on judge/court entities is inconsistent without
a legal-domain model (e.g. InLegalBERT-based NER), which is more setup than a 24-hour
window comfortably allows.

**Option C — LLM-assisted extraction, using the model already running inside
Antigravity.** Give it the first ~3 pages of extracted text plus the last page
(signature block) — not all 87 pages, both to bound tokens and because every field this
brief asks for lives in that header/footer/signature envelope, never in the 80-odd
pages of legal argument in between — and a tight instruction to return only the seven
keys above, `null` for anything not explicitly present, no guessing beyond the text
given. Strongest at exactly the two things regex struggles with here: merging the
CORAM line with the signature block for `judge_name`, and telling "Act cited as
precedent" apart from "Act named in the operative order" for the `act` field. Weakness:
non-determinism (mitigate by keeping temperature low and re-running once to check
stability) and it's one more moving part to explain in the write-up.

**Recommended: hybrid of A and C.**

1. Extract the text layer once (`pdfplumber` or `pdftotext -layout`; the font check
   already run confirms this PDF has a real, embedded text layer, so OCR is not
   needed).
2. Use regex against the repeated running header (`<Court> <CaseType> No.<Number> of
   <Year> dt.<date>`) for `court_name`, `case_number`, and `case_type` — this pattern
   is clean, appears dozens of times identically, and doesn't need an LLM.
3. Isolate three slices of the document: (a) the caption block up to and including the
   `CORAM:` line, (b) the paragraph(s) containing the final order (search for "set
   aside" / "ultra vires" / "quashed" / "allowed" near the last 1–2 pages), (c) the
   closing signature block after the final order. Hand just those slices — not the
   full 87 pages — to the model for `court_bench`, `judge_name`, and `act`/`section`,
   with the strict "use exactly these keys, null if absent, don't infer beyond what's
   in the text" instruction.
4. Merge the two results into one dict. Where regex and the model both produced
   `case_number`/`case_type`, prefer the regex value (deterministic) and note it if
   they ever disagreed.
5. `json.dump(result, f, indent=2, ensure_ascii=False)`.

This keeps the fully-deterministic fields fully deterministic, uses the model only for
the two fields that genuinely need judgment, and keeps the whole thing cheap even on
an 87-page input because only a few KB of text ever reaches the model.

## 5. Deliverables to produce in this project

- `extract_entities.py` — the pipeline (readable, with comments explaining each
  regex/slice, not a black box).
- `output.json` — the result dict for `vraj.pdf`, written by the script (not
  hand-edited afterward).
- `requirements.txt` — whatever you actually import (e.g. `pdfplumber`).
- `approach.md` — the write-up for submission. Cover: which of Options A/B/C (or the
  hybrid) you used and why; how each field was located (one line per field is enough);
  every judgment call from §3 that you resolved one way rather than another (especially
  `court_bench`); what you'd do differently if this had to run unattended across many
  differently-formatted case documents instead of just this one; and the actual time
  spent, tracked honestly rather than backfilled.

## 6. Before calling it done

- Run the script and confirm `output.json` has all seven keys, with `null` used (not
  missing keys, not empty strings) wherever nothing was found.
- Re-open `vraj.pdf` and manually check `case_number`, `judge_name`, and `act` against
  what the script produced — those are the three fields with the sharpest edge cases
  in §2, so they're the ones worth a manual second look before submitting.
- Keep `approach.md` short and specific rather than long and generic — a reviewer
  comparing several candidates' submissions will be looking for evidence you understood
  *this* document's quirks, not a generic essay on NLP techniques.

---

**Kickoff message to paste into Antigravity's chat/task box**, with `vraj.pdf` in the
same project folder as this brief:

> Read `antigravity_task_brief.md` in this folder and follow it fully. Build
> `extract_entities.py` using the recommended hybrid approach in §4, run it against
> `vraj.pdf`, and produce `output.json`, `requirements.txt`, and `approach.md` as
> described in §5. Verify against the checklist in §6 before telling me you're done.
