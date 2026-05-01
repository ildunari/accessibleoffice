# Rule Catalog

Each row maps a rule to its WCAG success criterion, severity, supported formats, and which fix stage handles it.

| rule_id | Severity | Formats | WCAG | Auto-fix stage | Plain impact |
|---|---|---|---|---|---|
| `alt-text-missing` | Error | docx, pptx | 1.1.1 | Stage 3 (AI) | Screen readers cannot describe this image. |
| `slide-title-missing` | Error | pptx | 2.4.6, 2.4.10 | Stage 3 (AI) | No slide title — navigation breaks. |
| `table-header-missing` | Error | docx, pptx | 1.3.1 | Stage 2 (heuristic) / Stage 4 | Screen readers can't announce headers. |
| `table-merged-cells` | Warning | docx, pptx | 1.3.1 | Stage 4 (judgment) | Merged cells confuse row/column relationships. |
| `link-text-generic` | Warning | docx, pptx | 2.4.4 | Stage 3 (AI) | "click here" gives no link purpose. |
| `heading-structure` | Warning | docx | 1.3.1, 2.4.6, 2.4.10 | Stage 4 | Skipped or fake headings break navigation. |
| `reading-order` | Warning | pptx | 1.3.2 | Stage 4 | Slide may read in confusing order. |
| `color-contrast` | IS | docx, pptx | 1.4.3 | Stage 4 | Text may be unreadable for low-vision users. |
| `nontext-contrast` | Tip | pptx | 1.4.11 | Stage 4 | Borders/UI elements invisible to low-vision users. |
| `decorative-flag-suggested` | Tip | pptx | 1.1.1 | Stage 4 | Decorative shapes should be marked to skip. |
| `document-title-missing` | Tip | docx, pptx | 2.4.2 | Stage 2 (filename-derived) | No document title — filename used. |
| `document-language-missing` | Tip | docx, pptx | 3.1.1 | Stage 2 (--default-lang opt-in) | Wrong pronunciation by screen readers. |
| `list-semantics-fake` | Tip | docx | 1.3.1 | Stage 4 | Typed-bullet "lists" not announced as lists. |
| `floating-object` | Warning | docx | 1.3.2 | Stage 4 | Floating images may be skipped or read out of order. |
| `captions-media-missing` | Warning | pptx | 1.2.1, 1.2.2 | Detect-only | Audio/video without captions excludes deaf users. |
| `drm-irm-detected` | Warning | docx, pptx | — | Detect-only | Rights-managed file; will not write changes. |

## Notes per rule

### `alt-text-missing`

Detects pictures, shapes, charts, groups, and SmartArt without meaningful alt text. Treats filename-style descriptors (`image.png`, `Picture 3`) as meaningless — those flag too. Skips shapes already marked `adec:decorative="1"`.

### `slide-title-missing`

A slide passes if it has a `<p:ph type="title">` (or `ctrTitle`) placeholder containing non-empty text. Off-canvas titles in placeholders still pass; "fake titles" in plain text boxes don't.

### `table-header-missing`

Word: looks for `w:tr/w:trPr/w:tblHeader` on the first row — NOT `tblLook/@firstRow` (that's a style hint).
PowerPoint: looks for `a:tblPr/@firstRow="1"`.
Stage 2 deterministic fix only fires if the first row visually looks like a header (bold runs, distinct fill).

### `color-contrast`

Resolves theme colors via `ThemeColorResolver`, computes WCAG contrast against assumed white background (PowerPoint v1 limitation). Threshold: 4.5:1 normal text, 3.0:1 large/bold text. Auto-darkening would change the design system, so all findings defer to stage 4.

### `decorative-flag-suggested`

Flags only empty shapes with preset geometries that are commonly decorative (`line`, `straightConnector1`, sometimes `rect`). Conservative — most cases defer to stage 4.

### `drm-irm-detected`

If a document is rights-managed, python-docx/python-pptx will fail to open it — so reaching detection means no IRM. This rule is a placeholder for future detection of in-OOXML protection markers.
