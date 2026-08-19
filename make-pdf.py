#!/usr/bin/env python3
"""Alexanders Brief -> a single archival A4 PDF.

    python3 make-pdf.py 2026-08-17 [--out DIR] [--maxw 1000] [--quality 82] [--cover]

What it does, in order:
  1. renders the day's brief JSON to the same self-contained page GitHub Pages serves
  2. pre-fetches every image referrer-less, exactly as the browser does, and reports
     any that have already rotted
  3. downscales and re-encodes them to data URIs
  4. builds a fully offline HTML: editorial theme forced, screen machinery gone,
     fonts inlined, print stylesheet appended
  5. prints it with Chrome **with the network hard-blocked**, which is the only
     honest proof the file is self-contained
  6. stamps a running footer with page numbers
  7. writes a PDF outline, sets document metadata, and compresses

Design notes worth keeping:
  * Chrome does not implement @page margin boxes, so `content: counter(page)` in CSS
    does nothing. The footer is therefore stamped onto the finished PDF instead.
  * CSS grid rows fragment badly across pages -- a row taller than the space left
    pushes the whole row over and leaves the rest of the page blank. The print
    stylesheet turns the story grid into multicol, which refills instead.
  * Images are embedded rather than linked because the web assets rot fast: on
    2026-08-18 one of 2026-08-17's own story images was already a 404 at DR.
    A PDF generated the same morning keeps the picture forever; one generated late
    inherits the hole.
"""
import argparse
import base64
import datetime
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

import fitz
from PIL import Image

def _env(name, default):
    """Paths are overridable so this runs somewhere other than one Mac.

    A cloud session gets the repo and nothing else: no `~/AI eksperimenter`, no
    Google Chrome in /Applications. Set BRIEF_ENGINE, BRIEF_PAGES and
    BRIEF_CHROME (or let the Chromium autodetect below find a Linux binary)."""
    return os.environ.get(name, default)


def _find_chrome():
    for p in (os.environ.get("BRIEF_CHROME"),
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
              "/usr/bin/google-chrome", "/usr/bin/chromium",
              "/usr/bin/chromium-browser", "/opt/google/chrome/chrome"):
        if p and os.path.exists(p):
            return p
    return "google-chrome"   # let PATH resolve it, and fail loudly if it cannot


REPO = _env("BRIEF_ENGINE", "/Users/alexanderbustrup/AI eksperimenter/News breif 1/.worktrees/news-brief-mvp")
PAGES = _env("BRIEF_PAGES", "/Users/alexanderbustrup/AI eksperimenter/alexanders-brief-pages")
CHROME = _find_chrome()
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

DK_DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
DK_MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli",
             "august", "september", "oktober", "november", "december"]


def danish_date(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{DK_DAYS[d.weekday()]} den {d.day}. {DK_MONTHS[d.month - 1]} {d.year}"


def step(n, total, msg):
    print(f"[{n}/{total}] {msg}", flush=True)


# ---------------------------------------------------------------- 1. render
def render_base(date, work):
    brief_json = os.path.join(PAGES, "data", f"{date}.json")
    if not os.path.exists(brief_json):
        sys.exit(f"no brief published for {date}: {brief_json} does not exist")
    base = os.path.join(work, "base.html")
    subprocess.run(
        ["npm", "run", "--silent", "render:static", "--", brief_json, base],
        cwd=REPO, check=True, capture_output=True, text=True,
    )
    return brief_json, base


# ---------------------------------------------------------------- 2. fetch
def fetch_images(brief, work):
    items = []
    for i, s in enumerate(brief["stories"]):
        if s.get("image"):
            items.append((f"story{i:02d}", s["image"]["url"]))
    for i, r in enumerate(brief.get("recommendations") or []):
        if r.get("image"):
            items.append((f"rec{i:02d}", r["image"]["url"]))
    if (brief.get("column") or {}).get("image"):
        items.append(("column", brief["column"]["image"]["url"]))

    imgdir = os.path.join(work, "img")
    os.makedirs(imgdir, exist_ok=True)
    dead = []
    for key, url in items:
        out = os.path.join(imgdir, key + ".orig")
        p = subprocess.run(
            ["curl", "-sSL", "--max-time", "30", "-A", UA, "-w", "%{http_code}",
             "-o", out, url],
            capture_output=True, text=True,
        )
        code = (p.stdout or "").strip()[-3:]
        if code != "200":
            if os.path.exists(out):
                os.remove(out)
            dead.append((key, code, url))
    if dead:
        print("     ROTTED ASSETS -- these stories will show the grey fallback box:")
        for key, code, url in dead:
            print(f"       {key} HTTP {code}  {url[:88]}")
    print(f"     {len(items) - len(dead)}/{len(items)} images retrieved")
    return items, dead


# ---------------------------------------------------------------- 3. encode
def encode_images(work, maxw, quality):
    imgdir = os.path.join(work, "img")
    data, before, after = {}, 0, 0
    for f in sorted(os.listdir(imgdir)):
        if not f.endswith(".orig"):
            continue
        key, path = f[:-5], os.path.join(imgdir, f)
        try:
            im = Image.open(path)
            im.load()
        except Exception as e:
            print(f"     undecodable {key}: {e}")
            continue
        before += os.path.getsize(path)
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        if w > maxw:
            im = im.resize((maxw, round(h * maxw / w)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=quality, optimize=True, subsampling=1)
        b = buf.getvalue()
        after += len(b)
        data[key] = "data:image/jpeg;base64," + base64.b64encode(b).decode()
    if before:
        print(f"     {before/1024:.0f} KB -> {after/1024:.0f} KB "
              f"({after/before:.0%}) at maxw={maxw} q={quality}")
    return data


# ---------------------------------------------------------------- 4. build
def build_offline(base, imgmap, css_path, out):
    h = open(base, encoding="utf-8").read()

    # Deterministic theme: never read the reader's stored layout preference.
    h, n = re.subn(r'var saved = localStorage\.getItem\("brief-layout"\);',
                   'var saved = "editorial";', h)
    if not n:
        print("     WARNING: layout-preference hook not found; theme may vary")

    m = re.search(r"(globalThis\.__BRIEF_DATA__ = )(\{.*?\})(;?\s*</script>)", h, re.S)
    d = json.loads(m.group(2))
    swapped = dropped = 0

    def apply(obj, key):
        nonlocal swapped, dropped
        if not obj.get("image"):
            return
        if key in imgmap:
            obj["image"]["url"] = imgmap[key]
            swapped += 1
        else:
            obj["image"] = None      # same graceful fallback the page already shows
            dropped += 1

    for i, s in enumerate(d["stories"]):
        apply(s, f"story{i:02d}")
    for i, r in enumerate(d.get("recommendations") or []):
        apply(r, f"rec{i:02d}")
    if d.get("column"):
        apply(d["column"], "column")
    h = h[:m.start()] + m.group(1) + json.dumps(d, ensure_ascii=False) + m.group(3) + h[m.end():]

    fonts_path = os.path.join(os.path.dirname(base), "fonts.css")
    fonts = open(fonts_path, encoding="utf-8").read()
    h = h.replace(
        '<link rel="preload" href="fonts.css" as="style" '
        'onload="this.onload=null;this.rel=\'stylesheet\'">',
        "<style>\n" + fonts + "\n</style>")
    h = re.sub(r'<noscript><link rel="stylesheet" href="fonts.css"></noscript>', "", h)

    css = open(css_path, encoding="utf-8").read()
    h = h.replace("</head>", '<style id="print-css">\n' + css + "\n</style>\n</head>")

    open(out, "w", encoding="utf-8").write(h)

    stripped = re.sub(r"data:image/[^\"')]+", "", h)
    hosts = sorted({re.match(r"https?://([^/]+)", u).group(1)
                    for u in re.findall(r"https?://[^\s\"')]+", stripped)})
    print(f"     images embedded {swapped}, fallen back {dropped}; "
          f"{len(hosts)} remote host(s) still referenced (links only)")
    return swapped, dropped


# ---------------------------------------------------------------- 5. print
def print_pdf(html, out):
    subprocess.run(
        [CHROME, "--headless", "--disable-gpu", "--no-sandbox", "--no-pdf-header-footer",
         "--virtual-time-budget=25000",
         # Hard-block the network. If anything still renders, it was truly embedded.
         "--host-resolver-rules=MAP * ~NOTFOUND", "--proxy-server=http://127.0.0.1:9",
         f"--print-to-pdf={out}", "file://" + html],
        capture_output=True, text=True,
    )
    if not os.path.exists(out):
        sys.exit("Chrome produced no PDF")


# ------------------------------------------------------- 6. footer + outline
def norm(s):
    s = unicodedata.normalize("NFC", s or "")
    s = s.replace("­", "").replace("-\n", "").replace("‐", "")
    return re.sub(r"[^0-9a-zA-Zæøåéüö]", "", s.lower())


def stamp_and_finish(raw, out, brief, date):
    doc = fitz.open(raw)
    total = doc.page_count
    left = danish_date(date).upper()

    # Chrome does not implement @page margin boxes, so the running footer is
    # stamped here rather than expressed in CSS.
    for i, page in enumerate(doc):
        # 22pt (7.8mm) clears every desktop printer's unprintable margin and reads
        # as a footer rather than as content that fell off the page.
        y = page.rect.height - 22
        page.insert_text((28, y), left, fontname="helv", fontsize=6.4,
                         color=(0.42, 0.39, 0.35))
        right = f"SIDE {i+1} AF {total}"
        w = fitz.get_text_length(right, fontname="helv", fontsize=6.4)
        page.insert_text((page.rect.width - 28 - w, y), right, fontname="helv",
                         fontsize=6.4, color=(0.42, 0.39, 0.35))

    pages = [norm(p.get_text()) for p in doc]

    def find_page(text):
        key = norm(text)[:38]
        if not key:
            return None
        for i, t in enumerate(pages):
            if key in t:
                return i
        short = norm(text)[:20]
        for i, t in enumerate(pages):
            if short and short in t:
                return i
        return None

    toc = []
    for s in brief["stories"]:
        p = find_page(s["headline"])
        if p is not None:
            toc.append([1, s["headline"][:110], p + 1])
    if brief.get("signals"):
        p = find_page(brief["signals"][0].get("headline") or brief["signals"][0]["text"])
        if p is not None:
            toc.append([1, "Kort fortalt", p + 1])
    if brief.get("column"):
        p = find_page(brief["column"]["title"])
        if p is not None:
            toc.append([1, "Klummen: " + brief["column"]["title"][:90], p + 1])
    if brief.get("recommendations"):
        p = find_page(brief["recommendations"][0]["title"])
        if p is not None:
            toc.append([1, "AI-redaktørens valg", p + 1])
    toc.sort(key=lambda e: e[2])
    if toc:
        doc.set_toc(toc)

    doc.set_metadata({
        "title": f"Alexanders Brief — {danish_date(date)}",
        "author": "Alexanders Brief (AI-redaktionen)",
        "subject": brief.get("dek", "")[:300],
        "keywords": ", ".join(sorted({t for s in brief["stories"] for t in s.get("topics", [])}))[:400],
        "creator": "make-pdf.py via Chrome headless",
        "producer": "PyMuPDF",
    })
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    return total, len(toc)


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--out", default="/tmp/pdfx/final")
    ap.add_argument("--maxw", type=int, default=1000)
    ap.add_argument("--quality", type=int, default=82)
    a = ap.parse_args()

    work = os.path.join(a.out, "work", a.date)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    css = os.path.join(os.path.dirname(os.path.abspath(__file__)), "print.css")

    step(1, 6, f"rendering the published page for {a.date}")
    brief_json, base = render_base(a.date, work)
    brief = json.load(open(brief_json, encoding="utf-8"))
    print(f"     {len(brief['stories'])} stories, {len(brief['signals'])} signals, "
          f"{len(brief.get('recommendations') or [])} recommendations, "
          f"column={bool(brief.get('column'))}, keyNumber={bool(brief.get('keyNumber'))}")

    step(2, 6, "pre-fetching images referrer-less")
    fetch_images(brief, work)

    step(3, 6, "downscaling and encoding to data URIs")
    imgmap = encode_images(work, a.maxw, a.quality)

    step(4, 6, "building the offline print HTML")
    html = os.path.join(work, "print.html")
    build_offline(base, imgmap, css, html)

    step(5, 6, "printing with the network hard-blocked")
    raw = os.path.join(work, "raw.pdf")
    print_pdf(html, raw)

    step(6, 6, "stamping footer, outline and metadata")
    out = os.path.join(a.out, f"Alexanders-Brief-{a.date}.pdf")
    pages, bookmarks = stamp_and_finish(raw, out, brief, a.date)

    size = os.path.getsize(out)
    print()
    print(f"WROTE {out}")
    print(f"      {pages} A4 pages, {size/1024/1024:.2f} MB, {bookmarks} bookmarks")
    return out


if __name__ == "__main__":
    main()
