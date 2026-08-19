#!/usr/bin/env python3
"""d4-opslag: the brief PDF with a designed front page.

A copy of make-pdf.py (the live one is untouched) with three additions:

  * publisher favicons are fetched, flattened onto the paper colour and
    embedded as data URIs, so the front page's source tags carry real logos;
  * fp.css + fp.js are injected, which compose page one and anchor every
    headline to its story;
  * after printing, the composed front page's own report (type scale, the
    headline sizes it actually used) is read back out of the DOM.

    python3 build.py 2026-08-17 [--out DIR]
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
import urllib.parse

import fitz
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
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
PAPER = (246, 239, 228)          # --bg #f6efe4, what logos are flattened onto

DK_DAYS = ["mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag", "søndag"]
DK_MONTHS = ["januar", "februar", "marts", "april", "maj", "juni", "juli",
             "august", "september", "oktober", "november", "december"]


def danish_date(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{DK_DAYS[d.weekday()]} den {d.day}. {DK_MONTHS[d.month - 1]} {d.year}"


def step(n, total, msg):
    print(f"[{n}/{total}] {msg}", flush=True)


def curl(url, out, timeout="15"):
    p = subprocess.run(["curl", "-sSL", "--max-time", timeout, "-A", UA,
                        "-w", "%{http_code}", "-o", out, url],
                       capture_output=True, text=True)
    return (p.stdout or "").strip()[-3:]


# ---------------------------------------------------------------- 1. render
def render_base(date, work):
    brief_json = os.path.join(PAGES, "data", f"{date}.json")
    if not os.path.exists(brief_json):
        sys.exit(f"no brief published for {date}: {brief_json} does not exist")
    base = os.path.join(work, "base.html")
    subprocess.run(["npm", "run", "--silent", "render:static", "--", brief_json, base],
                   cwd=REPO, check=True, capture_output=True, text=True)
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
        code = curl(url, out, "30")
        if code != "200":
            if os.path.exists(out):
                os.remove(out)
            dead.append((key, code, url))
    if dead:
        print("     ROTTED ASSETS -- these stories lose their picture:")
        for key, code, url in dead:
            print(f"       {key} HTTP {code}  {url[:88]}")
    print(f"     {len(items) - len(dead)}/{len(items)} images retrieved")
    return items, dead


# ------------------------------------------------------- 2b. publisher logos
def icon_candidates(host, work):
    """The publisher's own favicon, then whatever its homepage declares.

    Only the publisher's own domain is asked -- no third-party favicon proxy,
    which would be one more thing to trust and one more thing to rot.
    """
    yield f"https://{host}/favicon.ico"
    home = os.path.join(work, "home.html")
    if curl(f"https://{host}/", home) == "200":
        try:
            h = open(home, encoding="utf-8", errors="ignore").read(400000)
        except Exception:
            return
        for m in re.finditer(r"<link[^>]+>", h, re.I):
            tag = m.group(0)
            if not re.search(r'rel=["\'][^"\']*icon', tag, re.I):
                continue
            u = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
            if u:
                yield urllib.parse.urljoin(f"https://{host}/", u.group(1))


def flatten(im):
    """Composite onto the paper colour and square it up."""
    im = im.convert("RGBA")
    w, h = im.size
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), PAPER + (255,))
    canvas.paste(im, ((side - w) // 2, (side - h) // 2), im)
    return canvas.convert("RGB")


def has_ink(im):
    """Does anything actually show once it sits on cream paper?

    TechCrunch serves a 198-byte, fully transparent favicon.ico. Embedded
    blindly it prints as an empty box next to the publisher's name, which is
    worse than no logo at all -- so every candidate has to prove it carries
    ink before it is allowed onto the page.
    """
    px = list(im.resize((32, 32), Image.LANCZOS).getdata())
    diff = sum(abs(r - PAPER[0]) + abs(g - PAPER[1]) + abs(b - PAPER[2])
               for r, g, b in px) / (len(px) * 3.0)
    return diff >= 6.0


def fetch_logos(brief, work):
    wanted = {}
    for s in brief["stories"]:
        url = s.get("primarySourceUrl") or ""
        srcs = s.get("sources") or []
        src = next((x for x in srcs if x.get("url") == url), srcs[0] if srcs else None)
        if not src or not url:
            continue
        host = urllib.parse.urlparse(url).netloc
        if host:
            wanted.setdefault(src["publisher"], host)

    logodir = os.path.join(work, "logo")
    os.makedirs(logodir, exist_ok=True)
    out, missing = {}, []
    for pub, host in sorted(wanted.items()):
        best, best_side, tried = None, 0, 0
        for cand in icon_candidates(host, work):
            tried += 1
            if tried > 5:
                break
            path = os.path.join(logodir, re.sub(r"\W+", "_", pub) + f"_{tried}.bin")
            if curl(cand, path) != "200" or os.path.getsize(path) < 100:
                continue
            try:
                im = Image.open(path)
                im.load()
            except Exception:
                continue
            flat = flatten(im)
            if not has_ink(flat):
                continue
            side = min(im.size)
            if side > best_side:
                best, best_side = flat, side
            if best_side >= 48:
                break
        if best is None:
            missing.append((pub, host))
            continue
        buf = io.BytesIO()
        best.resize((64, 64), Image.LANCZOS).save(buf, "PNG", optimize=True)
        out[pub] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    print(f"     {len(out)}/{len(wanted)} publisher logos embedded"
          + (f"; name-only fallback for {', '.join(p for p, _ in missing)}" if missing else ""))
    return out


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
def build_offline(base, imgmap, logos, date, out, onepager=False):
    h = open(base, encoding="utf-8").read()

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
            obj["image"] = None
            dropped += 1

    for i, s in enumerate(d["stories"]):
        apply(s, f"story{i:02d}")
    for i, r in enumerate(d.get("recommendations") or []):
        apply(r, f"rec{i:02d}")
    if d.get("column"):
        apply(d["column"], "column")
    h = h[:m.start()] + m.group(1) + json.dumps(d, ensure_ascii=False) + m.group(3) + h[m.end():]

    fonts = open(os.path.join(os.path.dirname(base), "fonts.css"), encoding="utf-8").read()
    h = h.replace(
        '<link rel="preload" href="fonts.css" as="style" '
        'onload="this.onload=null;this.rel=\'stylesheet\'">',
        "<style>\n" + fonts + "\n</style>")
    h = re.sub(r'<noscript><link rel="stylesheet" href="fonts.css"></noscript>', "", h)

    css = open(os.path.join(HERE, "print.css"), encoding="utf-8").read()
    fpcss = open(os.path.join(HERE, "fp.css"), encoding="utf-8").read()
    h = h.replace("</head>", '<style id="print-css">\n' + css + "\n</style>\n"
                             '<style id="fp-css">\n' + fpcss + "\n</style>\n</head>")

    fpjs = open(os.path.join(HERE, "fp.js"), encoding="utf-8").read()
    boot = ("<script>globalThis.__FP_LOGOS__ = " + json.dumps(logos, ensure_ascii=False)
            + "; globalThis.__FP_DATE_DK__ = " + json.dumps(danish_date(date))
            + "; globalThis.__FP_ONEPAGER__ = " + ("true" if onepager else "false")
            + ";</script>\n<script>\n" + fpjs + "\n</script>\n")
    h = h.replace("</body>", boot + "</body>")

    open(out, "w", encoding="utf-8").write(h)

    stripped = re.sub(r"data:image/[^\"')]+", "", h)
    hosts = sorted({re.match(r"https?://([^/]+)", u).group(1)
                    for u in re.findall(r"https?://[^\s\"')]+", stripped)})
    print(f"     images embedded {swapped}, fallen back {dropped}; "
          f"{len(hosts)} remote host(s) still referenced (links only)")


# ---------------------------------------------------------------- 5. print
NET_BLOCK = ["--host-resolver-rules=MAP * ~NOTFOUND", "--proxy-server=http://127.0.0.1:9"]


def print_pdf(html, out):
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox",
                    "--no-pdf-header-footer", "--virtual-time-budget=25000"]
                   + NET_BLOCK + [f"--print-to-pdf={out}", "file://" + html],
                   capture_output=True, text=True)
    if not os.path.exists(out):
        sys.exit("Chrome produced no PDF")


def read_front_report(html):
    """Ask the same page what it did: which type scale it solved for, and the
    headline size it gave each story. Printing cannot return values, so the
    front page writes its report into the DOM and it is read back here."""
    p = subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-sandbox",
                        "--virtual-time-budget=20000", "--dump-dom"] + NET_BLOCK
                       + ["file://" + html], capture_output=True, text=True)
    m = re.search(r'<template id="fp-report" data-report="(.*?)"></template>', p.stdout, re.S)
    if not m:
        return None
    raw = (m.group(1).replace("&quot;", '"').replace("&amp;", "&")
           .replace("&lt;", "<").replace("&gt;", ">"))
    try:
        return json.loads(raw)
    except Exception:
        return None


# ------------------------------------------------------- 6. footer + outline
def norm(s):
    s = unicodedata.normalize("NFC", s or "")
    s = s.replace("­", "").replace("-\n", "").replace("‐", "")
    return re.sub(r"[^0-9a-zA-Zæøåéüö]", "", s.lower())


def explicit_gotos(doc, brief):
    """Rewrite Chrome's named destinations as explicit GoTo links, aimed at the
    headline rather than at the element.

    Two things are wrong with what Chrome writes. It is a /Dests name lookup
    (PyMuPDF calls that LINK_NAMED), one indirection away from the file. And it
    points at the top of the <article> box, which the print stylesheet's
    multicol happily leaves at the foot of the previous page: on 2026-08-06
    four of nine jumps landed one page early, above a headline the reader could
    not yet see. Each link is therefore re-aimed at the page and the height
    where that story's headline is actually printed.
    """
    heads = {}
    for i, s in enumerate(brief["stories"]):
        words = re.sub(r"\s+", " ", s["headline"]).split(" ")
        for pno in range(1, doc.page_count):
            hit = []
            for n in (7, 5, 3):
                hit = doc[pno].search_for(" ".join(words[:n]))
                if hit:
                    break
            if hit:
                heads["story-%d" % i] = (pno, max(0.0, hit[0].y0 - 14))
                break

    made, reaimed = 0, 0
    for page in doc:
        old = [l for l in page.get_links()
               if l["kind"] == fitz.LINK_NAMED and l.get("page", -1) >= 0]
        for l in old:
            page.delete_link(l)
        for l in old:
            aim = heads.get(l.get("nameddest"))
            if aim:
                target, to = aim[0], fitz.Point(0, aim[1])
                if target != l["page"]:
                    reaimed += 1
            else:
                # PyMuPDF is asymmetric here: reading a named destination hands
                # back the raw PDF point (origin bottom-left) while insert_link
                # flips whatever it is given (origin top-left).
                h = doc[l["page"]].rect.height
                pt = l.get("to") or fitz.Point(0, h)
                target, to = l["page"], fitz.Point(pt.x, h - pt.y)
            page.insert_link({"kind": fitz.LINK_GOTO, "from": l["from"],
                              "page": target, "to": to, "zoom": 0})
            made += 1
    return made, reaimed


def slim_front_page(doc, floor=120_000, quality=74):
    """Re-compress the front page's own pictures.

    Chrome re-renders any image it has to crop, at roughly 300 dpi and at a
    quality it does not expose -- the hero alone came out of the printer as a
    1.27 MB JPEG upscaled past its source. Those streams, and only those, are
    re-encoded here: the body pages still carry the pipeline's own q=82 files,
    which must not be compressed twice. Measured on 2026-08-17: 3.87 MB -> 1.56 MB
    with no visible loss at print size.
    """
    page = doc[0]
    saved = 0
    for x in page.get_images(full=True):
        info = doc.extract_image(x[0])
        if len(info["image"]) < floor:
            continue
        try:
            im = Image.open(io.BytesIO(info["image"])).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=quality, optimize=True, subsampling=1)
        except Exception:
            continue
        if len(buf.getvalue()) < len(info["image"]):
            page.replace_image(x[0], stream=buf.getvalue())
            saved += len(info["image"]) - len(buf.getvalue())
    return saved


def stamp_and_finish(raw, out, brief, date):
    doc = fitz.open(raw)
    total = doc.page_count
    left = danish_date(date).upper()

    for i, page in enumerate(doc):
        if i == 0:
            continue           # the front page carries its own foot rule
        y = page.rect.height - 22
        page.insert_text((28, y), left, fontname="helv", fontsize=6.4,
                         color=(0.42, 0.39, 0.35))
        right = f"SIDE {i+1} AF {total}"
        w = fitz.get_text_length(right, fontname="helv", fontsize=6.4)
        page.insert_text((page.rect.width - 28 - w, y), right, fontname="helv",
                         fontsize=6.4, color=(0.42, 0.39, 0.35))

    pages = [norm(p.get_text()) for p in doc]

    def find_page(text, skip_front=True):
        key = norm(text)[:38]
        if not key:
            return None
        rng = range(1, len(pages)) if skip_front else range(len(pages))
        for i in rng:
            if key in pages[i]:
                return i
        short = norm(text)[:20]
        for i in rng:
            if short and short in pages[i]:
                return i
        return None

    toc = [[1, "Forside", 1]]
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
        "creator": "build.py (d4-opslag) via Chrome headless",
        "producer": "PyMuPDF",
    })
    goto, reaimed = explicit_gotos(doc, brief)
    saved = slim_front_page(doc)
    doc.save(out, garbage=4, deflate=True, clean=True)
    doc.close()
    return total, len(toc), goto, reaimed, saved


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--out", default="/tmp/fp/d4-opslag")
    ap.add_argument("--name", default=None)
    ap.add_argument("--maxw", type=int, default=1000)
    ap.add_argument("--quality", type=int, default=82)
    ap.add_argument("--onepager", action="store_true",
                    help="output ONLY the front sheet: the whole edition on one page")
    a = ap.parse_args()

    work = os.path.join(a.out, "work", a.date)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    step(1, 7, f"rendering the published page for {a.date}")
    brief_json, base = render_base(a.date, work)
    brief = json.load(open(brief_json, encoding="utf-8"))
    print(f"     {len(brief['stories'])} stories, {len(brief['signals'])} signals, "
          f"{len(brief.get('recommendations') or [])} recommendations, "
          f"column={bool(brief.get('column'))}")

    step(2, 7, "pre-fetching images referrer-less")
    fetch_images(brief, work)

    step(3, 7, "fetching publisher logos")
    logos = fetch_logos(brief, work)

    step(4, 7, "downscaling and encoding to data URIs")
    imgmap = encode_images(work, a.maxw, a.quality)

    step(5, 7, "building the offline print HTML with the front page")
    html = os.path.join(work, "print.html")
    build_offline(base, imgmap, logos, a.date, html, a.onepager)
    shutil.copy(html, os.path.join(a.out, f"print-{a.date}.html"))

    step(6, 7, "printing with the network hard-blocked")
    raw = os.path.join(work, "raw.pdf")
    print_pdf(html, raw)
    rep = read_front_report(html)
    if rep:
        print(f"     front page scale --s = {rep['scale']}; {'; '.join(rep['notes'])}")
        for tier, size, imp, head in rep["sizes"]:
            print(f"       {tier:6s} {size*rep['scale']:5.1f}pt  importance {imp:3d}  {head}")
        json.dump(rep, open(os.path.join(a.out, f"fp-report-{a.date}.json"), "w"),
                  ensure_ascii=False, indent=1)
    else:
        print("     WARNING: the front page did not report back")

    if a.onepager:
        # The sheet is the edition: keep page one and discard the body. Doing it
        # here rather than by not rendering the body keeps one code path, and the
        # body still supplies the fonts and the print stylesheet the sheet uses.
        _d = fitz.open(raw)
        if _d.page_count > 1:
            _d.delete_pages(range(1, _d.page_count))
        _d.save(raw + ".solo.pdf", garbage=4, deflate=True, clean=True)
        _d.close()
        raw = raw + ".solo.pdf"

    step(7, 7, "stamping footer, outline and metadata")
    out = os.path.join(a.out, a.name or ("Alexanders-Brief-ETSIDE-" if a.onepager else "Alexanders-Brief-") + f"{a.date}.pdf")
    pages, bookmarks, goto, reaimed, saved = stamp_and_finish(raw, out, brief, a.date)

    print()
    print(f"WROTE {out}")
    print(f"      {pages} A4 pages, {os.path.getsize(out)/1024/1024:.2f} MB, "
          f"{bookmarks} bookmarks, {goto} internal GoTo links "
          f"({reaimed} re-aimed at the headline's own page)")
    print(f"      front-page pictures re-compressed, {saved/1024/1024:.2f} MB saved")
    return out


if __name__ == "__main__":
    main()
