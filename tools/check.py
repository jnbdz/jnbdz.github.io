#!/usr/bin/env python3
"""Verification gate for the site. Standard library only, no dependencies.

Run from the repository root:  python3 tools/check.py
"""

import html.parser
import pathlib
import re
import sys
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Pages this checker owns. Everything under projects/ and labs/ is legacy
# demo content that is deliberately left alone.
PAGES = ["index.html", "demos.html"]

failures = []
passes = []


def record(ok, name, detail=""):
    (passes if ok else failures).append((name, detail))


# --------------------------------------------------------------------------
# 1. Privacy: no contact data, no forbidden strings
# --------------------------------------------------------------------------

BUILTIN_FORBIDDEN = [
    (r"mailto:", "mailto: link"),
    (r"tel:", "tel: link"),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "email address"),
    (r"\b\d{3}[ .-]\d{3}[ .-]\d{4}\b", "phone number"),
    (r"\b\(\d{3}\)\s*\d{3}[ .-]\d{4}\b", "phone number"),
    (r"Montr[eé]al", "city name"),
]


def load_extra_patterns():
    """Read forbidden strings from an untracked file.

    The client name must never be committed, so it cannot be hardcoded in this
    script. Put one string per line in tools/private-patterns.txt (gitignored).
    Blank lines and lines starting with # are ignored.
    """
    path = ROOT / "tools" / "private-patterns.txt"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append((re.escape(line), "private pattern"))
    return out


def check_privacy(pages):
    patterns = BUILTIN_FORBIDDEN + load_extra_patterns()
    targets = [ROOT / p for p in pages] + [ROOT / "assets" / "css" / "main.css"]
    hits = []
    for path in targets:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, label in patterns:
            for m in re.finditer(pattern, text, re.IGNORECASE):
                line = text[: m.start()].count("\n") + 1
                hits.append(f"{path.name}:{line} {label} -> {m.group(0)!r}")
    record(not hits, "privacy", "; ".join(hits))


# --------------------------------------------------------------------------
# 2. Structure, accessibility, no-JS
# --------------------------------------------------------------------------


class PageParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.mains = 0
        self.headings = []          # list of int levels in document order
        self.imgs_without_alt = 0
        self.scripts = 0
        self.event_attrs = []
        self.js_urls = []
        self.links = []             # href/src values
        self.html_lang = None
        self.first_anchor_href = None
        self.metas = {}             # name/property -> content
        self.rel_links = {}         # rel -> href
        self.title_seen = False
        self._in_title = False
        self.title_text = ""
        self.ids = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)

        for key, value in attrs:
            if key.startswith("on"):
                self.event_attrs.append(f"<{tag} {key}>")
            if value and key in ("href", "src") and value.strip().lower().startswith("javascript:"):
                self.js_urls.append(value)

        if "id" in a and a["id"]:
            self.ids.add(a["id"])

        if tag == "html":
            self.html_lang = a.get("lang")
        elif tag == "main":
            self.mains += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.headings.append(int(tag[1]))
        elif tag == "img":
            if "alt" not in a:
                self.imgs_without_alt += 1
        elif tag == "script":
            self.scripts += 1
        elif tag == "title":
            self._in_title = True
            self.title_seen = True
        elif tag == "meta":
            key = a.get("name") or a.get("property")
            if key:
                self.metas[key] = a.get("content", "")
        elif tag == "link":
            rel = a.get("rel")
            if rel:
                self.rel_links[rel] = a.get("href", "")

        if tag == "a" and "href" in a:
            if self.first_anchor_href is None:
                self.first_anchor_href = a["href"]
            self.links.append(a["href"])
        elif tag in ("img", "script", "iframe") and "src" in a:
            self.links.append(a["src"])
        elif tag == "link" and "href" in a:
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title_text += data


def parse(page):
    p = PageParser()
    p.feed((ROOT / page).read_text(encoding="utf-8"))
    return p


def check_structure(page, parsers):
    p = parsers[page]

    record(p.mains == 1, f"{page}: exactly one <main>", f"found {p.mains}")
    record(bool(p.html_lang), f"{page}: html lang set", "missing lang attribute")

    h1s = [h for h in p.headings if h == 1]
    record(len(h1s) == 1, f"{page}: exactly one <h1>", f"found {len(h1s)}")

    skips = []
    prev = None
    for level in p.headings:
        if prev is not None and level > prev + 1:
            skips.append(f"h{prev} -> h{level}")
        prev = level
    record(not skips, f"{page}: no heading level skips", ", ".join(skips))

    record(p.imgs_without_alt == 0, f"{page}: every img has alt",
           f"{p.imgs_without_alt} missing")

    record(p.scripts == 0, f"{page}: no <script>", f"{p.scripts} found")
    record(not p.event_attrs, f"{page}: no inline event handlers",
           ", ".join(p.event_attrs))
    record(not p.js_urls, f"{page}: no javascript: URLs", ", ".join(p.js_urls))

    # Skip link must be the first anchor in the document.
    first = p.first_anchor_href or ""
    record(first.startswith("#"), f"{page}: skip link is first anchor",
           f"first anchor is {first!r}")
    if first.startswith("#"):
        record(first[1:] in p.ids, f"{page}: skip link target exists",
               f"no element with id={first[1:]!r}")


def check_meta(page, parsers):
    p = parsers[page]
    record(p.title_seen and p.title_text.strip(), f"{page}: has <title>", "missing or empty")
    record(len(p.title_text.strip()) <= 60, f"{page}: title under 60 chars",
           f"{len(p.title_text.strip())} chars")
    record("viewport" in p.metas, f"{page}: has viewport meta", "missing")
    viewport = p.metas.get("viewport", "")
    bad_zoom = "user-scalable=no" in viewport or "maximum-scale" in viewport
    record(not bad_zoom, f"{page}: viewport allows zoom", viewport)
    record(bool(p.metas.get("description")), f"{page}: has description meta", "missing")
    record("canonical" in p.rel_links, f"{page}: has canonical link", "missing")
    for prop in ("og:title", "og:description", "og:url", "og:type"):
        record(prop in p.metas, f"{page}: has {prop}", "missing")


# --------------------------------------------------------------------------
# 3. Links resolve
# --------------------------------------------------------------------------


def check_links(page, parsers):
    p = parsers[page]
    broken = []
    for href in p.links:
        href = href.strip()
        if not href or href.startswith("#"):
            continue
        parsed = urllib.parse.urlparse(href)
        # Anything with a scheme (http, https, mailto, tel, data) is not a
        # local path. mailto/tel are forbidden outright by check_privacy.
        if parsed.scheme:
            continue
        path = parsed.path
        if not path:
            continue
        target = (ROOT / path.lstrip("/")).resolve()
        if path.endswith("/"):
            ok = (target / "index.html").exists()
        else:
            ok = target.exists() or (target / "index.html").exists()
        if not ok:
            broken.append(href)
    record(not broken, f"{page}: local links resolve", ", ".join(broken))


# --------------------------------------------------------------------------


def main():
    # Optional page arguments let a task check only what exists yet:
    #   python3 tools/check.py index.html
    pages = sys.argv[1:] or PAGES

    missing = [p for p in pages if not (ROOT / p).exists()]
    if missing:
        print(f"FAIL setup: missing pages: {', '.join(missing)}")
        return 1

    parsers = {page: parse(page) for page in pages}

    check_privacy(pages)
    for page in pages:
        check_structure(page, parsers)
        check_meta(page, parsers)
        check_links(page, parsers)

    for name, _ in passes:
        print(f"PASS {name}")
    for name, detail in failures:
        print(f"FAIL {name}: {detail}")

    print(f"\n{len(passes)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
