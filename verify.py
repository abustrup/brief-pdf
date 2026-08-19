#!/usr/bin/env python3
"""Independent defect scan for a brief PDF, checked against its source JSON.

Written separately from the build agents on purpose: it trusts nothing they
reported and re-derives every number from the PDF and the brief data.

    python3 verify.py <brief.pdf> <brief.json>
"""
import json
import re
import sys

import fitz

SCREEN_CHROME = [
    "I DAG", "UGEN", "MÅNEDEN", "ÅRET", "ARKIV", "VIS SOM", "STANDARD",
    "MAGASIN", "FEED", "MØRK", "HØR BRIEFEN", "Gem til senere", "Gemt til senere",
    "Hvorfor?", "Mere som dette", "Mindre som dette", "Spring til indhold",
]
DANISH = ["æ", "ø", "å", "Æ", "Ø", "Å", "»", "«"]


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def dehyphenate(s):
    """Undo Chrome's line-break hyphenation before matching.

    CSS `hyphens: auto` makes Chrome insert a real hyphen (U+2010, or U+00AD)
    at the break, so the PDF text layer holds "Zucker- berg" where the source
    says "Zuckerberg". Matching against the raw layer produces false misses --
    and the same artifact is what a reader gets when they copy text out.
    """
    s = s or ""
    s = re.sub(r"[­‐‑-]\s*\n\s*", "", s)
    s = re.sub(r"[­‐‑]\s+", "", s)
    s = re.sub(r"[­‐‑]", "-", s)
    return s


def main(pdf_path, json_path):
    doc = fitz.open(pdf_path)
    brief = json.load(open(json_path, encoding="utf8"))
    pages = doc.page_count
    text = "\n".join(p.get_text() for p in doc)
    flat = norm(dehyphenate(text))
    hyphen_artifacts = len(re.findall(r"[\u00ad\u2010\u2011]\s", text))
    defects = []
    notes = []

    # --- geometry -------------------------------------------------------
    r = doc[0].rect
    w_mm, h_mm = r.width * 25.4 / 72, r.height * 25.4 / 72
    is_a4 = abs(w_mm - 210) < 2 and abs(h_mm - 297) < 2
    if not is_a4:
        defects.append(("major", f"page size {w_mm:.0f}x{h_mm:.0f}mm is not A4 (210x297)"))

    sizes = {(round(p.rect.width), round(p.rect.height)) for p in doc}
    if len(sizes) > 1:
        defects.append(("minor", f"inconsistent page sizes: {sizes}"))

    # --- screen chrome --------------------------------------------------
    # Case-SENSITIVE with word boundaries. The nav labels are uppercased by CSS
    # and land uppercase in the text layer, whereas a case-insensitive substring
    # match on "I DAG" also hits the ordinary Danish word inside "dagens" --
    # which it did, on all four variants, before this was tightened.
    for probe in SCREEN_CHROME:
        pat = re.compile(r"(?<![\wÆØÅæøå])" + re.escape(probe) + r"(?![\wÆØÅæøå])")
        for i, p in enumerate(doc):
            if pat.search(p.get_text()):
                defects.append(("blocker", f'screen chrome "{probe}" on page {i+1}'))
                break

    # --- content parity -------------------------------------------------
    missing = []
    for s in brief.get("stories", []):
        if norm(s["headline"])[:45].lower() not in flat.lower():
            missing.append(("story headline", s["headline"][:60]))
    for sig in brief.get("signals", []):
        h = sig.get("headline") or sig.get("text")
        if norm(h)[:40].lower() not in flat.lower():
            missing.append(("signal", h[:60]))
    col = brief.get("column")
    if col:
        if norm(col["title"])[:35].lower() not in flat.lower():
            missing.append(("column title", col["title"][:60]))
        if norm(col["uncertainty"])[:45].lower() not in flat.lower():
            missing.append(("column uncertainty", col["uncertainty"][:60]))
    for rec in brief.get("recommendations") or []:
        if norm(rec["title"])[:35].lower() not in flat.lower():
            missing.append(("recommendation", rec["title"][:60]))
    kn = brief.get("keyNumber")
    if kn and norm(kn["label"])[:30].lower() not in flat.lower():
        missing.append(("keyNumber label", kn["label"][:60]))
    for kind, what in missing:
        defects.append(("blocker", f"missing {kind}: {what}"))

    # --- links ----------------------------------------------------------
    wanted = set()
    for s in brief.get("stories", []):
        if s.get("primarySourceUrl"):
            wanted.add(s["primarySourceUrl"])
    for sig in brief.get("signals", []):
        wanted.add(sig["url"])
    for rec in brief.get("recommendations") or []:
        wanted.add(rec["url"])

    annots = [(i + 1, l) for i, p in enumerate(doc) for l in p.get_links()]
    uris = {l["uri"] for _, l in annots if l.get("uri")}
    hit = sum(1 for u in wanted if u in uris)
    if wanted and hit < len(wanted):
        for u in sorted(wanted - uris)[:6]:
            defects.append(("major", f"link never appears as a clickable annotation: {u[:80]}"))

    for pno, l in annots:
        if not l.get("uri"):
            continue
        rect = l.get("from")
        if rect is None or rect.is_empty or rect.width < 1 or rect.height < 1:
            defects.append(("major", f"zero-area link rect on page {pno}: {l['uri'][:60]}"))
        elif not fitz.Rect(doc[pno - 1].rect).intersects(rect):
            defects.append(("major", f"link rect off-page on page {pno}: {l['uri'][:60]}"))

    # --- images ---------------------------------------------------------
    want_imgs = sum(1 for s in brief.get("stories", []) if s.get("image"))
    want_imgs += sum(1 for r in (brief.get("recommendations") or []) if r.get("image"))
    got = [(i + 1, x) for i, p in enumerate(doc) for x in p.get_images(full=True)]
    tiny = 0
    for pno, x in got:
        try:
            info = doc.extract_image(x[0])
            if info["width"] < 40 or info["height"] < 40:
                tiny += 1
        except Exception:
            pass
    if tiny:
        defects.append(("minor", f"{tiny} embedded image(s) under 40px — likely tracking pixels or placeholders"))
    if want_imgs and len(got) < want_imgs:
        defects.append(("major", f"{len(got)} images embedded but the brief carries {want_imgs}"))

    # --- blank / thin pages ---------------------------------------------
    thin = [i + 1 for i, p in enumerate(doc)
            if len(p.get_text().strip()) < 40 and not p.get_images()]
    if thin:
        defects.append(("major", f"near-blank page(s): {thin}"))

    # --- content near the physical edge ---------------------------------
    EDGE = 12  # pts
    FOOTER_BAND = 30  # pts from the foot: the stamped running footer lives here
    clipped = []
    for i, p in enumerate(doc):
        pr = p.rect
        for b in p.get_text("blocks"):
            x0, y0, x1, y1 = b[:4]
            if y0 > pr.height - FOOTER_BAND:
                continue  # the running footer, by design
            if x0 < EDGE or y0 < EDGE or x1 > pr.width - EDGE or y1 > pr.height - EDGE:
                clipped.append(i + 1)
                break
    if clipped:
        defects.append(("major", f"text within {EDGE}pt of the page edge on pages {sorted(set(clipped))[:8]}"))

    # --- stranded headline (heading last on page, body overleaf) --------
    stranded = []
    for i, p in enumerate(doc):
        blocks = [b for b in p.get_text("blocks") if b[4].strip()
                  and b[1] <= p.rect.height - 30]  # ignore the running footer
        if len(blocks) < 2:
            continue
        last = blocks[-1]
        body_bottom = p.rect.height - 90
        if last[3] > body_bottom and len(norm(last[4])) < 110 and not norm(last[4]).endswith("."):
            stranded.append(i + 1)
    if stranded:
        defects.append(("minor", f"possible stranded headline at foot of page(s) {stranded[:8]}"))

    # --- Danish glyphs ---------------------------------------------------
    for ch in DANISH:
        if ch not in text:
            notes.append(f'Danish character "{ch}" not found in extracted text')
    if "�" in text or "□" in text:
        defects.append(("blocker", "replacement/tofu glyph present — a font failed to embed"))

    # --- report ----------------------------------------------------------
    import os
    size = os.path.getsize(pdf_path)
    print(f"FILE      {pdf_path}")
    print(f"BRIEF     {brief.get('date')}  ({len(brief.get('stories', []))} stories, "
          f"{len(brief.get('signals', []))} signals, {len(brief.get('recommendations') or [])} recs, "
          f"column={bool(col)}, keyNumber={bool(kn)})")
    print(f"PAGES     {pages}   SIZE {size/1024/1024:.2f} MB   "
          f"{'A4' if is_a4 else f'{w_mm:.0f}x{h_mm:.0f}mm'}")
    print(f"LINKS     {len(uris)} distinct URIs, {len(annots)} annotations; "
          f"{hit}/{len(wanted)} of the brief's own URLs clickable")
    print(f"IMAGES    {len(got)} embedded (brief carries {want_imgs})")
    print(f"TEXT      {len(text):,} chars")
    print(f"FONTS     {len({f[3] for p in doc for f in p.get_fonts(full=True)})} embedded")
    if hyphen_artifacts:
        print(f"  note: {hyphen_artifacts} hyphenation artifact(s) in the text layer — copied text will read \"Zucker- berg\"")
    for n in notes:
        print(f"  note: {n}")
    print()
    order = {"blocker": 0, "major": 1, "minor": 2}
    defects.sort(key=lambda d: order[d[0]])
    if not defects:
        print("DEFECTS   none found")
    else:
        print(f"DEFECTS   {len(defects)}")
        for sev, what in defects:
            print(f"  [{sev:7}] {what}")
    blockers = sum(1 for s, _ in defects if s == "blocker")
    print()
    print("VERDICT   " + ("FAIL" if blockers else "PASS (no blockers)"))
    return 1 if blockers else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
