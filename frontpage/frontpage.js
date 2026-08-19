/* ==========================================================================
   d4-opslag — builds the PDF's front page from the same __BRIEF_DATA__ the
   body pages are rendered from, then anchors every headline to its story.

   Runs inside the offline print HTML, after the site's own renderer has put
   the stories in the DOM. Three jobs:

     1. give every rendered story article an id (story-0, story-1, ...) so
        Chrome can turn the front page's "#story-3" links into real internal
        PDF GoTo annotations. Without the id there is no annotation at all.
     2. compose the front page and put it first in the document.
     3. solve for one type-scale --s so the composition fills exactly one A4
        sheet, whether the day has four stories or nine. Nothing is cut.
   ========================================================================== */
(function () {
  var B = globalThis.__BRIEF_DATA__;
  var LOGOS = globalThis.__FP_LOGOS__ || {};
  var DATE_DK = globalThis.__FP_DATE_DK__ || "";
  var SOLO = !!globalThis.__FP_ONEPAGER__;
  var report = { sizes: [], scale: null, hero: null, notes: [] };

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function arrow(dir) {
    var NS = "http://www.w3.org/2000/svg";
    var svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "fp-arrow fp-arrow-" + dir);
    svg.setAttribute("aria-hidden", "true");
    var g = document.createElementNS(NS, "path");
    // out = north-east (leaves the document); down = into the document
    g.setAttribute("d", dir === "out"
      ? "M7 17 L17 7 M9.5 7 L17 7 L17 14.5"
      : "M12 5 L12 17 M6.5 11.5 L12 18 L17.5 11.5");
    g.setAttribute("fill", "none");
    g.setAttribute("stroke", "currentColor");
    g.setAttribute("stroke-width", "2.6");
    g.setAttribute("stroke-linecap", "round");
    g.setAttribute("stroke-linejoin", "round");
    svg.appendChild(g);
    return svg;
  }
  function norm(s) {
    return (s || "").replace(/\s+/g, " ").trim().toLowerCase()
      .replace(/[^0-9a-zæøåéüö ]/g, "");
  }
  function sourceFor(story) {
    var srcs = story.sources || [];
    var m = null, i;
    for (i = 0; i < srcs.length; i++) {
      if (srcs[i].url === story.primarySourceUrl) { m = srcs[i]; break; }
    }
    return m || srcs[0] || null;
  }
  function publisher(story) {
    var s = sourceFor(story);
    return s ? s.publisher : null;
  }

  /* ---- 1. anchors on the body's story articles --------------------------- */
  function anchorStories() {
    var arts = Array.prototype.slice.call(
      document.querySelectorAll("article.story, article.story-card, article.story-lead"));
    var used = {}, hit = 0;
    B.stories.forEach(function (s, i) {
      var key = norm(s.headline).slice(0, 40), j;
      for (j = 0; j < arts.length; j++) {
        if (used[j]) continue;
        var h = arts[j].querySelector(".story-headline");
        if (h && norm(h.textContent).indexOf(key) === 0) {
          arts[j].id = "story-" + i; used[j] = 1; hit++; return;
        }
      }
    });
    report.notes.push("anchored " + hit + "/" + B.stories.length + " story articles");
    return hit;
  }

  /* ---- 2. the front page ------------------------------------------------- */
  function tagRow(story, opts) {
    opts = opts || {};
    var row = el("div", "fp-tags" + (opts.stack ? " fp-tags-stack" : ""));

    var line1 = el("div", "fp-tagline");
    // The hero and the band already print the section as a kicker above the
    // headline, so repeating it as a chip below spends two lines saying one
    // thing on a page that is short of vertical space. The tail has no kicker,
    // so it still needs the chip.
    if (!opts.noSection) {
      line1.appendChild(el("span", "fp-tag fp-tag-sec", (story.section || "").toUpperCase()));
    }
    if (story.isOutsideProfile) {
      line1.appendChild(el("span", "fp-tag fp-tag-out",
        opts.compact ? "Uden for profil" : "Uden for din profil"));
    }
    row.appendChild(line1);

    var line2 = opts.stack ? el("div", "fp-tagline") : line1;

    if (opts.jumpHref && !SOLO) {
      var jump = el("a", "fp-tag fp-jump");
      jump.appendChild(arrow("down"));
      jump.appendChild(el("span", null, "Læs her"));
      jump.href = opts.jumpHref;
      line2.appendChild(jump);
    }

    var pub = publisher(story);
    if (pub) {
      var chip = story.primarySourceUrl ? el("a", "fp-tag fp-tag-pub")
                                        : el("span", "fp-tag fp-tag-pub");
      if (story.primarySourceUrl) chip.href = story.primarySourceUrl;
      if (LOGOS[pub]) {
        var img = document.createElement("img");
        img.src = LOGOS[pub];
        img.alt = "";
        chip.appendChild(img);
      }
      chip.appendChild(el("span", null, pub));
      chip.appendChild(arrow("out"));
      line2.appendChild(chip);
    }

    var imp = (story.scores || {}).importance;
    if (typeof imp === "number") {
      var box = el("span", "fp-imp" + (imp >= 80 ? " hot" : ""));
      var tr = el("span", "track");
      var fill = document.createElement("i");
      fill.style.width = Math.max(6, Math.min(100, imp)) + "%";
      tr.appendChild(fill);
      box.appendChild(tr);
      // The score is stored 0-100; it reads as a rating on a 1-10 scale, with a
      // Danish decimal comma, so 84 shows as 8,4.
      var ten = Math.max(1, Math.min(10, imp / 10));
      var shown = ten.toFixed(1).replace(".", ",");
      box.appendChild(el("span", null, (opts.compact ? "" : "VIGTIGHED ") + shown));
      line2.appendChild(box);
    }
    if (opts.stack) row.appendChild(line2);
    return row;
  }

  /* Copy-fitting: a headline twice as long as its neighbour cannot be set at
     the same size in the same column. The penalty is kept small so importance,
     not length, stays the thing the reader sees in the size. */
  function copyfit(text, base, soft, hard) {
    var n = (text || "").length;
    if (n > hard) return base * 0.87;
    if (n > soft) return base * 0.94;
    return base;
  }

  function media(story, cls, href) {
    if (!story.image || !story.image.url) return null;
    var a = el("a", cls);
    a.href = href;
    var img = document.createElement("img");
    img.src = story.image.url;
    img.alt = story.image.alt || "";
    a.appendChild(img);
    return a;
  }

  function build() {
    var stories = B.stories || [];
    if (!stories.length) return;

    var imps = stories.map(function (s) { return (s.scores || {}).importance || 0; });
    var lo = Math.min.apply(null, imps), hi = Math.max.apply(null, imps);
    function t(s) {
      var v = (s.scores || {}).importance || 0;
      return hi === lo ? 1 : (v - lo) / (hi - lo);
    }

    /* Hero: the editorially chosen lead. The day's highest raw importance can
       sit elsewhere (2026-08-17: Ebola 90 vs lead 84) but that story is flagged
       isOutsideProfile, i.e. the ranker itself calls it important-in-general
       rather than important-to-him -- and the body pages open with the lead, so
       a front page that shouts a different story sends the reader to the wrong
       spread. Importance still drives every size below the hero. */
    var heroIdx = 0;
    stories.forEach(function (s, i) { if (s.isLead) heroIdx = i; });
    var hero = stories[heroIdx];
    report.hero = { headline: hero.headline, importance: (hero.scores || {}).importance,
                    isLead: !!hero.isLead, maxImportance: hi };

    var rest = stories.map(function (s, i) { return { s: s, i: i }; })
      .filter(function (x) { return x.i !== heroIdx; })
      .sort(function (a, b) { return ((b.s.scores || {}).importance || 0) - ((a.s.scores || {}).importance || 0); });

    var secN = Math.min(3, rest.length);
    var sec = rest.slice(0, secN), tail = rest.slice(secN);

    var anyImage = stories.some(function (s) { return s.image && s.image.url; });

    var fp = el("div", "fp measuring");
    if (!anyImage) fp.className += " noimg";

    fp.appendChild(el("div", "fp-rule-top"));

    var mast = el("header", "fp-mast");
    var wm = el("h1", "fp-wordmark");
    wm.appendChild(document.createTextNode("Alexanders "));
    var emph = el("em", null, "Brief");
    wm.appendChild(emph);
    mast.appendChild(wm);
    var right = el("div", "fp-mast-right");
    right.appendChild(el("p", "fp-date", DATE_DK.toUpperCase()));
    mast.appendChild(right);
    fp.appendChild(mast);

    /* --- hero ------------------------------------------------------------ */
    var heroSec = el("section", "fp-hero");
    var hm = media(hero, "fp-hero-media", "#story-" + heroIdx);
    if (hm) {
      if (hero.image.credit) hm.appendChild(el("span", "fp-credit", hero.image.credit));
      heroSec.appendChild(hm);
    }
    var ht = el("div", "fp-hero-text");
    ht.appendChild(el("p", "fp-flag", "Dagens historie"));
    ht.appendChild(el("p", "fp-kicker", (hero.section || "").toUpperCase()));
    var hlA = el("a", null);
    hlA.href = SOLO ? (hero.primarySourceUrl || "#") : ("#story-" + heroIdx);
    var hl = el("h2", "fp-hero-hl", hero.headline);
    var heroBase = (hm ? 27 : 34);
    var heroSize = copyfit(hero.headline, heroBase, 62, 84);
    hl.style.fontSize = "calc(" + heroSize.toFixed(2) + "pt * var(--s))";
    report.sizes.push(["hero", heroSize, (hero.scores || {}).importance, hero.headline.slice(0, 40)]);
    hlA.appendChild(hl);
    ht.appendChild(hlA);
    if (hero.subheadline) ht.appendChild(el("p", "fp-stand", hero.subheadline));
    ht.appendChild(tagRow(hero, { stack: true , noSection: true, jumpHref: "#story-" + heroIdx}));
    heroSec.appendChild(ht);
    fp.appendChild(heroSec);
    fp.appendChild(el("div", "fp-spacer"));

    /* --- second tier ----------------------------------------------------- */
    if (sec.length) {
      var band = el("section", "fp-band fp-band-" + sec.length);
      sec.forEach(function (x) {
        var card = el("article", "fp-sec");
        var m = media(x.s, "fp-sec-media", "#story-" + x.i);
        if (m) card.appendChild(m);
        else card.className += " fp-sec-nophoto";
        card.appendChild(el("p", "fp-kicker", (x.s.section || "").toUpperCase()));
        var a = el("a", null);
        a.href = SOLO ? (x.s.primarySourceUrl || "#") : ("#story-" + x.i);
        var h = el("h3", "fp-sec-hl", x.s.headline);
        var size = copyfit(x.s.headline, 13.6 * (0.82 + 0.36 * t(x.s)), 66, 88);
        h.style.fontSize = "calc(" + size.toFixed(2) + "pt * var(--s))";
        report.sizes.push(["tier2", size, (x.s.scores || {}).importance, x.s.headline.slice(0, 40)]);
        a.appendChild(h);
        card.appendChild(a);
        card.appendChild(tagRow(x.s, { stack: true , noSection: true, jumpHref: "#story-" + x.i}));
        band.appendChild(card);
      });
      fp.appendChild(band);
      fp.appendChild(el("div", "fp-spacer"));
    }

    /* --- tail ------------------------------------------------------------ */
    if (tail.length) {
      var list = el("section", "fp-tail" + (tail.length === 1 ? " fp-tail-1" : ""));
      list.appendChild(el("p", "fp-brief-label", "Også i dag"));
      tail.forEach(function (x) {
        var it = el("article", "fp-item");
        var m = media(x.s, "fp-item-media", "#story-" + x.i);
        if (m) it.appendChild(m);
        var tx = el("div", null);
        var a = el("a", null);
        a.href = SOLO ? (x.s.primarySourceUrl || "#") : ("#story-" + x.i);
        var h = el("h3", "fp-item-hl", x.s.headline);
        var size = copyfit(x.s.headline, 9.5 * (0.86 + 0.30 * t(x.s)), 70, 96);
        h.style.fontSize = "calc(" + size.toFixed(2) + "pt * var(--s))";
        report.sizes.push(["tail", size, (x.s.scores || {}).importance, x.s.headline.slice(0, 40)]);
        a.appendChild(h);
        tx.appendChild(a);
        tx.appendChild(tagRow(x.s, { compact: true, stack: true, jumpHref: "#story-" + x.i }));
        it.appendChild(tx);
        list.appendChild(it);
      });
      fp.appendChild(list);
      fp.appendChild(el("div", "fp-spacer"));
    }

    /* --- foot ------------------------------------------------------------ */
    /* --- everything else in the edition, when this sheet is the edition ---- */
    if (SOLO) {
      var extra = el("section", "fp-solo");
      var sig = (B.signals || []);
      if (sig.length) {
        extra.appendChild(el("h2", "fp-solo-h", "Kort fortalt"));
        var ul = el("div", "fp-solo-list");
        sig.forEach(function (g) {
          var a = el("a", "fp-solo-item");
          a.href = g.url;
          a.appendChild(el("span", "fp-solo-pub", (g.publisher || "").toUpperCase()));
          a.appendChild(el("span", null, g.headline || g.text));
          ul.appendChild(a);
        });
        extra.appendChild(ul);
      }
      if (B.column) {
        var kl = el("div", "fp-solo-kl");
        kl.appendChild(el("span", "fp-solo-pub", "KLUMMEN"));
        kl.appendChild(el("span", "fp-solo-kl-t", B.column.title));
        extra.appendChild(kl);
      }
      fp.appendChild(extra);
    }

    var foot = el("footer", "fp-foot");
    foot.appendChild(el("span", null, SOLO
      ? "Klik en overskrift eller en udgiverknap for at \u00e5bne artiklen hos kilden"
      : "Sort knap: l\u00e6s historien inde i briefen \u00b7 Hvid knap: \u00e5bn artiklen hos udgiveren"));
    foot.appendChild(el("span", "fp-foot-mark", "AI-redaktionen"));
    fp.appendChild(foot);

    document.body.insertBefore(fp, document.body.firstChild);

    /* --- 3. solve for the type scale ------------------------------------- */
    var lowS = 0.55, highS = 1.45, best = lowS, k;
    for (k = 0; k < 16; k++) {
      var mid = (lowS + highS) / 2;
      fp.style.setProperty("--s", mid.toFixed(4));
      fp.offsetHeight;
      if (fp.scrollHeight <= fp.clientHeight + 0.5) { best = mid; lowS = mid; }
      else { highS = mid; }
    }
    fp.style.setProperty("--s", best.toFixed(4));
    fp.classList.remove("measuring");
    report.scale = Number(best.toFixed(4));
    report.notes.push("overflow after fit: " + (fp.scrollHeight - fp.clientHeight) + "px");

    var tpl = document.createElement("template");
    tpl.id = "fp-report";
    tpl.setAttribute("data-report", JSON.stringify(report));
    document.body.appendChild(tpl);
  }

  var tries = 0;
  function boot() {
    var n = document.querySelectorAll(".story-headline").length;
    if (n < (B.stories || []).length && tries++ < 120) { setTimeout(boot, 40); return; }
    anchorStories();
    var ready = (document.fonts && document.fonts.ready)
      ? document.fonts.ready : Promise.resolve();
    ready.then(function () { requestAnimationFrame(build); });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
