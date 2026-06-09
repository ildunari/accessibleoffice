# Officecli Cookbook (Accessibility Subset)

Only the officecli operations a11yfix actually uses. For the full CLI surface see [iOfficeAI/OfficeCLI wiki](https://github.com/iOfficeAI/OfficeCli/wiki).

## Path syntax

1-based, positional. Use `[@id=...]` where stable IDs exist (preferred for multi-op edits since positional indices shift on insert/delete).

```
/slide[3]/picture[@id=41]
/slide[3]/shape[@id=42]
/body/p[2]/r[1]
/body/tbl[1]/tr[1]
```

## Set alt text on a picture

```bash
officecli set deck.pptx "/slide[3]/picture[@id=41]" --prop alt="A red square"
officecli set deck.pptx "/slide[3]/shape[@id=4]" --prop alt="Decorative chevron"
```

`alt` is a first-class property on picture, shape, chart, and group elements.

## Set table header row

```bash
# PowerPoint
officecli set deck.pptx "/slide[2]/table[@id=9]" --prop firstRow=1

# Word (per-row tblHeader semantic)
officecli set doc.docx "/body/tbl[1]/tr[1]" --prop header=true
```

## Set slide title

```bash
officecli set deck.pptx "/slide[3]" --prop title="Quarterly Earnings"
```

## Set core property

```bash
officecli set doc.docx "/document/coreProperties/title" --prop value="My Report"
officecli set doc.docx "/document/settings/themeFontLang" --prop value="en-US"
```

## Reorder shapes for reading order

```bash
officecli swap deck.pptx "/slide[3]/shape[@id=4]" "/slide[3]/shape[@id=7]"
officecli move deck.pptx "/slide[3]/shape[@id=4]" --before "/slide[3]/shape[@id=2]"
```

## Mark image as decorative (raw-XML fallback)

The decorative flag lives in the `adec` extension namespace and isn't in the schema, so use `raw-set` against the slide XML.

```bash
officecli raw-set deck.pptx /slides/slide3 --xml-patch ...  # see wiki for full syntax
```

## Validate after a batch

```bash
officecli validate deck.pptx --json
```

Exits 0 + `{"errors": []}` on schema-clean documents. Always run after a structural edit.

## Batch mode

Atomicity: **not atomic**. Successful commands flush even if a later one fails. a11yfix's wrapper handles this by snapshot-restoring on validate failure.

```json
[
  {"command":"set","path":"/slide[3]/picture[@id=41]","props":{"alt":"A red square"}},
  {"command":"set","path":"/slide[3]/table[@id=9]","props":{"firstRow":"1"}}
]
```

```bash
officecli batch deck.pptx --input ops.json --json
```
