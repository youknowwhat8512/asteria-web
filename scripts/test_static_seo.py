#!/usr/bin/env python3
"""Validate crawler-facing SEO in an already rendered Asteria public build."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse

ORIGIN = "https://asteria.club"
# Coach credentials are factual but snippet-hostile: each coach card suppresses
# exactly its career/meta/note block, and nothing else on the page is tagged.
NOSNIPPET_BLOCKS = ("coach-career", "coach-meta", "coach-note")
COACH_CARDS = 2
ROOT = Path(__file__).resolve().parents[1]
BUILD = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path.home() / ".local/share/asteria-web-public"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.meta: dict[str, list[str]] = {}
        self.canonicals: list[str] = []
        self.json_ld: list[dict] = []
        self._json_parts: list[str] | None = None
        self.links: list[str] = []
        self.h1 = 0
        self.paragraphs = 0
        # class name -> element count, and the class list of every element that
        # carries data-nosnippet. Both are structural (attribute-parsed), so the
        # snippet-suppression contract is asserted without regex over raw HTML.
        self.class_counts: Counter[str] = Counter()
        self.nosnippet: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()
        self.class_counts.update(classes)
        if "data-nosnippet" in attr_map:
            self.nosnippet.append(classes)
        if tag == "title":
            self.in_title = True
        elif tag == "meta":
            key = attr_map.get("name") or attr_map.get("property")
            if key and attr_map.get("content"):
                self.meta.setdefault(key, []).append(attr_map["content"] or "")
        elif tag == "link" and "canonical" in (attr_map.get("rel") or "").split():
            self.canonicals.append(attr_map.get("href") or "")
        elif tag == "script" and attr_map.get("type") == "application/ld+json":
            self._json_parts = []
        elif tag == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"] or "")
        elif tag == "h1":
            self.h1 += 1
        elif tag == "p":
            self.paragraphs += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "script" and self._json_parts is not None:
            raw = "".join(self._json_parts).strip()
            if raw:
                value = json.loads(raw)
                if isinstance(value, dict):
                    self.json_ld.append(value)
            self._json_parts = None

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self._json_parts is not None:
            self._json_parts.append(data)

    @property
    def title(self) -> str:
        return "".join(self.title_parts).strip()


def load_articles() -> list[dict]:
    node = shutil.which("node")
    assert node, "node is required to evaluate magazine/articles.js"
    loader = (
        "const fs=require('fs'),vm=require('vm');const s={window:{}};vm.createContext(s);"
        "new vm.Script(fs.readFileSync(process.argv[1],'utf8')).runInContext(s);"
        "process.stdout.write(JSON.stringify(s.window.ASTERIA_ARTICLES));"
    )
    result = subprocess.run(
        [node, "-e", loader, str(BUILD / "magazine" / "articles.js")],
        check=True,
        capture_output=True,
        text=True,
    )
    articles = json.loads(result.stdout)
    assert isinstance(articles, list) and articles, "articles.js must contain episodes"
    return articles


def parse_page(relative: str) -> PageParser:
    path = BUILD / relative
    assert path.is_file(), f"missing rendered page: {path}"
    parser = PageParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def nodes(page: PageParser) -> list[dict]:
    result: list[dict] = []
    for block in page.json_ld:
        graph = block.get("@graph")
        if isinstance(graph, list):
            result.extend(item for item in graph if isinstance(item, dict))
        else:
            result.append(block)
    return result


def types(page: PageParser) -> set[str]:
    found: set[str] = set()
    for node in nodes(page):
        value = node.get("@type")
        if isinstance(value, list):
            found.update(str(item) for item in value)
        elif value:
            found.add(str(value))
    return found


def one(values: list[str], label: str) -> str:
    assert len(values) == 1, f"{label}: expected exactly one value, got {values}"
    assert values[0].strip(), f"{label}: value must not be empty"
    return values[0]


def validate_page(page: PageParser, canonical: str, label: str, og_type: str) -> None:
    assert page.title, f"{label}: missing title"
    one(page.meta.get("description", []), f"{label} description")
    robots = one(page.meta.get("robots", []), f"{label} robots").replace(" ", "").lower()
    assert "index" in robots and "follow" in robots, f"{label}: robots must permit indexing and following"
    assert one(page.canonicals, f"{label} canonical") == canonical
    required = {
        "og:type", "og:locale", "og:site_name", "og:title", "og:description", "og:url",
        "og:image", "og:image:alt", "twitter:card", "twitter:title", "twitter:description",
        "twitter:image", "twitter:image:alt",
    }
    missing = sorted(key for key in required if len(page.meta.get(key, [])) != 1)
    assert not missing, f"{label}: missing or duplicate social metadata: {missing}"
    assert one(page.meta["og:type"], f"{label} og:type") == og_type
    assert one(page.meta["og:url"], f"{label} og:url") == canonical
    one(page.meta["og:title"], f"{label} og:title")
    assert one(page.meta["twitter:title"], f"{label} twitter:title") == one(page.meta["og:title"], f"{label} og:title")
    assert one(page.meta["twitter:description"], f"{label} twitter:description") == one(page.meta["description"], f"{label} description")
    assert one(page.meta["twitter:image"], f"{label} twitter:image") == one(page.meta["og:image"], f"{label} og:image")


def main() -> int:
    assert BUILD.is_dir(), f"build root does not exist: {BUILD}"
    articles = load_articles()
    expected_urls = {f"{ORIGIN}/", f"{ORIGIN}/magazine/"}
    expected_urls.update(f"{ORIGIN}{article['url']}" for article in articles)

    sitemap = ET.parse(BUILD / "sitemap.xml").getroot()
    namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    sitemap_rows: dict[str, ET.Element] = {}
    for row in sitemap.findall("sm:url", namespace):
        loc = row.findtext("sm:loc", namespaces=namespace)
        assert loc and loc not in sitemap_rows, f"duplicate or empty sitemap URL: {loc}"
        sitemap_rows[loc] = row
    assert set(sitemap_rows) == expected_urls, "sitemap URLs must exactly match home, magazine, and articles.js"
    for article in articles:
        loc = f"{ORIGIN}{article['url']}"
        lastmod = sitemap_rows[loc].findtext("sm:lastmod", namespaces=namespace)
        assert lastmod == article.get("modifiedAt", article["publishedAt"]), f"{loc}: lastmod is not data-backed"

    robots_text = (BUILD / "robots.txt").read_text(encoding="utf-8")
    assert re.search(r"(?im)^User-agent:\s*\*$", robots_text)
    assert re.search(r"(?im)^Allow:\s*/$", robots_text)
    assert re.search(r"(?im)^Sitemap:\s*https://asteria\.club/sitemap\.xml$", robots_text)

    home = parse_page("index.html")
    validate_page(home, f"{ORIGIN}/", "home", "website")
    assert {"WebSite", "SportsClub"}.issubset(types(home)), "home: WebSite and SportsClub JSON-LD required"
    assert home.h1 >= 1 and home.paragraphs >= 3, "home: meaningful static headings and text required"
    assert "/magazine/" in home.links, "home: static magazine link required"

    home_description = one(home.meta["description"], "home description")
    for key in ("og:description", "twitter:description"):
        assert one(home.meta[key], f"home {key}") == home_description, \
            f"home: {key} must repeat the meta description verbatim"
    club = next(node for node in nodes(home) if node.get("@type") == "SportsClub")
    assert club.get("description") == home_description, \
        "home: SportsClub JSON-LD description must repeat the meta description verbatim"

    expected_nosnippet = COACH_CARDS * len(NOSNIPPET_BLOCKS)
    assert len(home.nosnippet) == expected_nosnippet, \
        f"home: expected exactly {expected_nosnippet} data-nosnippet elements, found {len(home.nosnippet)}"
    for block in NOSNIPPET_BLOCKS:
        assert home.class_counts[block] == COACH_CARDS, \
            f"home: expected {COACH_CARDS} .{block} elements, found {home.class_counts[block]}"
        tagged = sum(1 for classes in home.nosnippet if block in classes)
        assert tagged == COACH_CARDS, \
            f"home: every .{block} must carry data-nosnippet ({tagged}/{COACH_CARDS} tagged)"
    stray = sorted(" ".join(classes) or "<unclassed element>" for classes in home.nosnippet
                   if not set(classes) & set(NOSNIPPET_BLOCKS))
    assert not stray, f"home: data-nosnippet must stay on coach credential blocks only; also found on {stray}"

    magazine = parse_page("magazine/index.html")
    validate_page(magazine, f"{ORIGIN}/magazine/", "magazine", "website")
    assert {"CollectionPage", "ItemList", "BreadcrumbList"}.issubset(types(magazine)), \
        "magazine: CollectionPage, ItemList and BreadcrumbList JSON-LD required"
    item_list = next(node for node in nodes(magazine) if node.get("@type") == "ItemList")
    items = item_list.get("itemListElement")
    assert isinstance(items, list) and len(items) == len(articles), "magazine ItemList must enumerate every episode"
    assert [item.get("position") for item in items] == list(range(1, len(items) + 1)), "ItemList positions must be contiguous"
    for article in articles:
        assert article["url"] in magazine.links, f"magazine: missing static link to {article['url']}"
    assert magazine.h1 == 1 and magazine.paragraphs >= len(articles), "magazine: meaningful no-JS content required"

    titles = {home.title, magazine.title}
    descriptions = {one(home.meta["description"], "home description"), one(magazine.meta["description"], "magazine description")}
    for article in articles:
        relative = f"magazine/{article['slug']}/index.html"
        canonical = f"{ORIGIN}{article['url']}"
        page = parse_page(relative)
        validate_page(page, canonical, article["slug"], "article")
        assert {"BlogPosting", "BreadcrumbList"}.issubset(types(page)), f"{article['slug']}: structured data types missing"
        post = next(node for node in nodes(page) if node.get("@type") == "BlogPosting")
        assert post.get("headline") == article["titleKo"]
        assert post.get("description") == article["excerpt"]
        assert post.get("datePublished", "").startswith(article["publishedAt"])
        assert post.get("dateModified", "").startswith(article.get("modifiedAt", article["publishedAt"]))
        assert post.get("mainEntityOfPage", {}).get("@id") == canonical
        assert page.h1 == 1 and page.paragraphs >= 3, f"{article['slug']}: meaningful static article body required"
        assert "/magazine/" in page.links and "/" in page.links, f"{article['slug']}: breadcrumb/navigation links missing"
        title = page.title
        description = one(page.meta["description"], f"{article['slug']} description")
        assert title not in titles, f"duplicate page title: {title}"
        assert description not in descriptions, f"duplicate page description: {description}"
        titles.add(title)
        descriptions.add(description)

    print(
        f"OK: SEO validated for home + magazine + {len(articles)} episodes; "
        "metadata, JSON-LD, sitemap, robots, and no-JS content are consistent"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, json.JSONDecodeError, ET.ParseError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
