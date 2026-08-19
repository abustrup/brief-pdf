# brief-pdf — a daily brief as a single PDF

Turns one day of a published brief into either a **multi-page PDF** with a
designed front page, or a **one-page edition** carrying the whole day on a single
A4 sheet. Images are embedded, every source is a working link, and the file needs
no network once built.

Built 2026-08-18 for *Alexanders Brief*. Nothing here is reader-specific — it
reads a published brief's JSON and renders it.

## Run it

```bash
python3 make-pdf.py 2026-08-17                 # body pages only
python3 frontpage/build.py 2026-08-17          # front page + body
python3 frontpage/build.py 2026-08-17 --onepager   # the whole edition on one sheet
python3 verify.py out.pdf brief.json           # measure a finished PDF
```

Needs Python 3 with **PyMuPDF (`fitz`)** and **Pillow**, and a Chromium. Paths
default to one particular Mac; override them anywhere else:

```bash
export BRIEF_ENGINE=/path/to/engine-checkout    # supplies npm run render:static
export BRIEF_PAGES=/path/to/pages-repo          # supplies data/YYYY-MM-DD.json
export BRIEF_CHROME=/usr/bin/chromium           # autodetected on Linux if unset
```

## How it works

Chrome headless prints the same self-contained page the site already serves, so
the layout, typography and links come along for free and there is no second
template to keep in sync. Then:

1. every image is pre-fetched referrer-less, downscaled and embedded as a data URI
2. the page prints **with the network hard-blocked** — the only honest proof the
   file is self-contained
3. a running footer, a PDF outline and document metadata are stamped on afterwards

The front page is composed in the browser (`frontpage/frontpage.js` + `.css`) and
carries every headline, sized by importance, with the publisher's own favicon and
two distinct buttons per story: a black one that jumps to the story inside the
document, and a white one that opens the article at the source. In `--onepager`
mode there is no inside to jump to, so the black button disappears and the space
goes to the day's short signals and the column instead.

## Five things that cost real time, so they don't have to again

**CSS grid fragments badly across page breaks.** A grid row taller than the space
left is pushed whole onto the next page, leaving the rest blank. Switching the
story grid to multicol took one edition from 16 pages to 8.

**Chrome does not implement `@page` margin boxes.** `content: counter(page)` does
nothing, so page numbers are stamped onto the finished PDF with PyMuPDF instead.

**Chrome leaves the `@page` margin unpainted**, so any background stops short of
the trim and prints a white border. Set the page margin to zero and move the
margin onto the content, where it actually gets painted.

**Anchor links become named destinations, not GoTo annotations.** `#foo` links
come out as PyMuPDF `kind 4` with a resolved page, which viewers follow fine —
but they only exist inside one printed document, so they are lost across a merge.
Where the front page is merged in separately, the jumps are inserted explicitly
as `LINK_GOTO` afterwards. Checking for `kind == 1` and concluding "internal
links failed" is the wrong test.

**Unicode arrows at small sizes print as hairlines.** ↗ set at 5 pt from a
fallback font is present in the text layer and invisible on paper beside bold
caps. Draw arrows as SVG with a real stroke weight instead — and note that a
check which counts glyphs will pass while the page looks broken.

## Verifying

`verify.py` opens a finished PDF and measures it rather than trusting the build:
page count and size, external links against the brief's own URLs, embedded image
count, near-blank pages, text clipped at the page edge, stranded headlines, and
Danish characters surviving in the text layer.

Two of its own false positives are worth knowing about, because both were found
by looking at rendered pixels rather than at the text layer: a case-insensitive
search for the nav label `"I DAG"` also matches the ordinary Danish word inside
*"i dagens historie"*, and a stamped page footer trips both the edge-margin and
stranded-headline checks unless the footer band is excluded.

## Samples

`samples/` carries 23 June 2026 only — a day whose stories have no photographs at
all. The other days embed the publishers' own images, and committing those would
rehost someone else's pictures rather than hotlink them, so they are gitignored.
Generate them locally instead.
