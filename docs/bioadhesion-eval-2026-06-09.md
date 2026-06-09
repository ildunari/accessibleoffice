# Real-world evaluation — Bioadhesion 2024 course decks (2026-06-09)

Evaluation of the `accessibleoffice` pipeline (v0.2.0) against real lecture material
from `~/Desktop/compliences courses/BIOL 2024/.../Bioadhesion 2024`, cross-checked
against an independent OOXML audit. officecli 1.0.72, claude both on PATH.

## Test corpus

| File | Type | Size | Slides/parts |
|---|---|---|---|
| EM Presentation non confidential.pptx | PPTX | 10.4 MB | 29 slides, 29 pictures |
| HISTO-GI 2015.pptx | PPTX | 22.3 MB | 27 slides, 57 pictures |
| Bioadhesion 2014 2024.pptx | PPTX | 95.4 MB | 176 slides, 261 pictures, 7 tables |
| Homework 5 / 6 Bioadhesion 24.docx | DOCX | ~18 KB | text only, no images |
| Spherics original short.ppt | legacy PPT | 2.3 MB | (rejection test) |

All work done on `/tmp` copies; originals never touched.

## Headline scan results (scan mode, no writes)

| Deck | Stage-1 findings | Top rules |
|---|---|---|
| EM | 633 | decorative-suggested 351, nontext-contrast 197, alt-missing 25, reading-order 20, color-contrast 19, slide-title 14 |
| HISTO-GI | 55 | reading-order 25, slide-title-missing 24, alt-missing 6 |
| Bioadhesion | 903 | alt-missing 363, decorative 325, reading-order 79, alt-generic 50, nontext-contrast 49, slide-title 33 |

---

## What works well (verified against manual audit)

- **slide-title-missing** — exact match to independent count: 14 / 24 / 33 title-less slides. Off-canvas gotcha handling holds up.
- **table-header-missing** — Bioadhesion has 7 tables; 6 mark `firstRow="1"`, exactly 1 (slide 60) does not. The system flagged exactly that one. No false positive/negative.
- **alt-text-missing** for truly-empty `descr` and Windows drive-letter paths (`C:\Users\...`) is correct.
- **legacy .ppt** — clean rejection, `[error] Unsupported file type: .ppt`, exit code **4** (non-zero, CI-safe).
- **reading-order** — sane heuristic (≥3 positioned shapes, z-order ≠ spatial order, inversions ≥ max(2, n/2)), correctly WARNING-level with "may not match author intent."

---

## Issues found, by severity

### P1 — Confirmed bugs (broken functionality)

#### 1. `document-title` deterministic fix is broken against officecli 1.0.72
`rules/document_title.py:64-70` emits:
```
set /document/coreProperties/title  props={"value": <stem>}
```
officecli 1.0.72 rejects this: `{"error":"Path not found: /document/coreProperties/title","code":"not_found"}`.
The title is a property on the **root** node (`format.title`); the correct op is:
```
set /  props={"title": <stem>}
```
Verified: with the corrected op, a clean `officecli batch` persists `<dc:title>` to
`docProps/core.xml` on disk. With the current op the batch fails silently (`ok=false`),
so `stage_2_fixes_applied = 0`, and a re-scan still reports `document-title-missing`.

**Fix:** change the op in `document_title.fix_deterministic` to `path="/"`,
`props={"title": ...}`. Two errors to correct: the path *and* the prop key (`value` → `title`).

#### 2. `alt-text-generic` regexes are ASCII-only → miss obvious filenames
`rules/alt_text.py:52-53` use `[A-Za-z0-9 _.\-]`. A `descr` of
`βgalExpressionInRatTissue.png` (literally ends in `.png`) matches **neither**
`_FILENAME_RE` nor `_PATH_RE` because the `β` falls outside the class — so it is
classified as neither *missing* nor *generic* and passes as acceptable alt text.

**Fix:** make the character classes Unicode-aware (e.g. allow `\w` with `re.UNICODE`,
or match on the extension suffix independently of the stem charset).

### P2 — Coverage gaps / false negatives

#### 3. `alt-text-generic` under-detects auto-filename alt text (the big one)
Most images in these decks *have* a `descr`, but it is the source filename/scan ID that
PowerPoint auto-populated — useless to a screen reader. Running the **actual** classifier
(`_is_missing_alt` / `_alt_quality_reason`) over every real value:

| Deck | pics | classified MISSING | GENERIC | **PASSED as OK** |
|---|---|---|---|---|
| EM | 29 | 19 | 4 | **6** |
| HISTO-GI | 57 | 6 | 0 | **51** |
| Bioadhesion | 261 | 199 | 50 | **12** |

**HISTO-GI is the smoking gun: 51 of 57 images pass** with scan numbers like
`001 - 14_01`, `007 - 14_03b`. The deck is reported as having 0 alt-text-quality warnings
when in reality essentially every image lacks a meaningful description.

Four distinct classes slip through:
1. **Scan/catalog numbers** — `001 - 14_01`, `018`, `019`, `1`, `F06-09` (no regex covers these).
2. **Filenames with no recognized extension** — `PS_091006_Rat_1_200nm_PJ_5min_16bit_005`, `fasa tem 4683`, `zein1 … 000022D`.
3. **Non-ASCII filenames** — `βgalExpressionInRatTissue.png` (bug #2).
4. **PDF-extraction auto-names** — `page1image23120112`, plus single generic words (`processing`).

**Impact compounds with stage 3:** AI alt-text regeneration is driven off the findings.
Anything not flagged as missing/generic never reaches the regenerator — so a *full* run
would still leave all 51 HISTO-GI images reading `001 - 14_01`.

**Fix:** extend `_alt_quality_reason` with (a) Unicode-aware filename/path matching,
(b) a "bare filename token" heuristic (single token, underscores/digit-runs, no natural
words; or trailing scanner-ID code), (c) numeric/catalog patterns
(`^[\d\W]+$`, `^\d+\s*-\s*\w+$`), (d) PDF auto-name pattern (`^page\d+image\d+$`).
Guard against over-flagging legitimate short captions — drive this with regression
fixtures built from the exact real-world values above.

#### 4. `color-contrast` is conservative by design → misses most real issues
The rule skips any run whose foreground or background color cannot be fully resolved
(documented limitation). On Bioadhesion, of 1934 text runs: 481 have explicit `srgbClr`,
171 use `schemeClr` (theme), and **1282 (66%) carry no explicit run color** and inherit
from layout/master — all skipped. Result: 2 contrast findings on a 176-slide deck.
Multi-master decks also resolve everything against `theme1.xml` only.

**Not a bug** (avoids false positives), but real contrast problems are largely invisible.
**Fix options:** resolve `schemeClr` against the theme to recover ~171 runs; associate
slides with their actual master/theme; and surface the coverage ("contrast evaluated on
N of M runs") so the result isn't read as "no contrast problems."

### P3 — Design / UX (not bugs, but they mislead users)

#### 5. Default `auto` mode is a near no-op on real content decks
Only three rules are deterministically fixable: `document-title` (broken, #1),
`table-header-missing` (conditional on a visually-header heuristic), and
`document-language-missing` (only with explicit `--default-lang`). Image, contrast,
reading-order, and slide-title issues — the bulk of real findings — all defer to AI/human.

On EM, `--mode auto` applied **0 fixes and left the file byte-identical**. The README
("Default mode is auto") and the desktop pitch ("Drag a file … it scans + auto-fixes")
oversell what `auto` does for a typical deck.

**Fix:** report honestly when no deterministic fix is available
("0 deterministic fixes; N issues need full mode / manual review"), and align README +
desktop copy with that reality.

#### 6. officecli round-trips and stamps the package even when 0 fixes land
On HW5 `--mode auto`: 8 internal parts were rewritten (`document.xml`, `styles.xml`,
`theme1.xml`, `numbering.xml`, `settings.xml`, `app.xml`, `[Content_Types].xml`,
`_rels/.rels`) and a new `docProps/custom.xml` was added stamping `OfficeCLI.Version`
and `OfficeCLI.LastModified` — while `stage_2_fixes_applied = 0` and the title finding
persisted. The original is backed up to `.bak`, but the **output** file is mutated with
zero accessibility benefit (diff noise, possible rendering drift on a "safe" tool).

**Fix:** when all pending ops fail or produce no net change, restore from backup so the
output is byte-identical to the input; only ship the round-tripped file when a fix
actually applied.

### P4 — Minor

- **decorative-flag-suggested is noisy** — 351 TIPs on EM. Verified real (the deck has
  347 empty `line` shapes), but a wall of 351 tips drowns signal. Consider collapsing to
  a per-deck summary.
- **reading-order inversion count is crude** — counts every displaced position, so a
  single adjacent swap counts as 2. Cosmetic; threshold still reasonable.
- **group-nested shapes** — `decorative_flag` and `alt_text` iterate `slide_xml.iter(p:sp)`,
  which includes shapes nested in `grpSp` (13 in EM). `ppt_target_ref` paths are computed
  against the spTree top level; nested shapes may get an off path. Low impact — add a
  targeted test with a grouped-shape fixture.

---

## Recommended fix order

1. **#1 document-title op** — one-line correctness fix, fully confirmed; add an officecli
   round-trip + persistence assertion as a regression test (would have caught the drift).
2. **#2 + #3 alt-text-generic** — highest user-facing value; TDD with the real Bioadhesion/
   HISTO-GI/EM values as fixtures. This is where the tool's stated stage-3 value lives.
3. **#6 no-op mutation** — restore-from-backup when nothing applied; cheap, removes a
   "safe tool mutated my file" footgun.
4. **#5 auto-mode honesty** — reporting + docs copy.
5. **#4 contrast coverage** — larger effort; scheme-color resolution + coverage reporting.

A smoke test that runs each deterministic rule's op against the *installed* officecli and
asserts success **and on-disk persistence** would have caught #1 the moment officecli
changed its path model / `--value`→`--prop` interface.

---

## Resolution (2026-06-09)

All actionable items fixed, TDD, full suite green (147 passed) + ruff clean.

- **#1 document-title** — op now targets root `/` with a `title` prop
  (`document_title.py`). Verified end-to-end: `--mode auto` on a docx now persists
  `dc:title`. Locked by `tests/integration/test_officecli_deterministic_ops.py`
  (runs against the real officecli, asserts on-disk persistence — skips if officecli absent).
- **#2 + #3 alt-text-generic** — Unicode-aware filename regex + new heuristics for
  catalog/scan numbers, extension-less filename tokens, PDF auto-names, and trailing
  scanner IDs (`alt_text.py`). HISTO-GI alt-generic went **0 → 51**; the junk that was
  passing as acceptable is now flagged. Driven by `tests/unit/test_alt_text_quality.py`
  (real deck values as fixtures, plus a false-positive guard set).
- **#6 no-op mutation** — the deterministic fixer now restores from backup when nothing
  applied, so a no-op run leaves the file byte-identical (`deterministic.py`). Verified:
  EM `--mode auto` is now byte-identical to input.
- **#5 auto-mode honesty** — the report ends with a `What's left:` breakdown
  (AI-fixable / deterministic / manual) and points to `--mode full`
  (`reporting/terminal.py`, capability sets in `rules/base.py`). README + desktop copy updated.
- **#4 contrast** — *correction to this report:* the rule **already** resolves `schemeClr`
  against the theme; that was not a gap. The genuine remaining limitations (layout/master
  inheritance, multi-master per-slide theme association) are now documented in the rule and
  intentionally deferred — guessing inherited colors would create false positives in a rule
  that never auto-fixes. The new report footer buckets contrast under manual-review, so a
  low finding count is no longer mistaken for "clean".
- **P4 correction:** group-nested shape paths were flagged as a possible concern but are in
  fact handled correctly by `ppt_target_ref` / `_group_scope` and already covered by tests
  (`test_alt_text.py`, `test_table_headers.py`). No change needed. Decorative tips are
  already aggregated in the by-rule table; the new footer labels them judgment calls.
