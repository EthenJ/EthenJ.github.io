#!/usr/bin/env python3
"""Validate the static site metadata and discovery artifacts without dependencies."""

from __future__ import annotations

import json
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
CANONICAL_URL = "https://ethenj.com/"
SCHOLAR_URL = "https://scholar.google.com/citations?user=WudbxWkAAAAJ"
GITHUB_URL = "https://github.com/EthenJ"
SITEMAP_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
REQUIRED_META = {
    ("name", "description"): None,
    ("property", "og:title"): None,
    ("property", "og:description"): None,
    ("property", "og:type"): "website",
    ("property", "og:site_name"): "Yicheng Jiang",
    ("property", "og:url"): CANONICAL_URL,
    ("property", "og:image"): "https://ethenj.com/assets/social-preview.jpg",
    ("property", "og:image:type"): "image/jpeg",
    ("property", "og:image:alt"): None,
    ("name", "twitter:card"): "summary_large_image",
    ("name", "twitter:title"): None,
    ("name", "twitter:description"): None,
    ("name", "twitter:url"): CANONICAL_URL,
    ("name", "twitter:image"): "https://ethenj.com/assets/social-preview.jpg",
    ("name", "twitter:image:alt"): None,
}
PUBLICATION_URLS = {
    "https://arxiv.org/abs/2605.21258",
    "https://arxiv.org/abs/2602.09878",
    "https://doi.org/10.1109/IEDM50854.2024.10873318",
}


class DocumentParser(HTMLParser):
    """Collect metadata, links, visible text, and JSON-LD blocks from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.canonicals: list[str] = []
        self.meta: dict[tuple[str, str], str] = {}
        self.links: set[str] = set()
        self.text: list[str] = []
        self.json_ld_blocks: list[str] = []
        self._inside_json_ld = False
        self._json_ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "link" and attributes.get("rel") == "canonical":
            self.canonicals.append(attributes.get("href", ""))
        if tag == "meta":
            if attributes.get("name"):
                self.meta[("name", attributes["name"])] = attributes.get("content", "")
            if attributes.get("property"):
                self.meta[("property", attributes["property"])] = attributes.get("content", "")
        if tag == "a" and attributes.get("href"):
            self.links.add(attributes["href"])
        if tag == "script" and attributes.get("type") == "application/ld+json":
            self._inside_json_ld = True
            self._json_ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._inside_json_ld:
            self.json_ld_blocks.append("".join(self._json_ld_parts))
            self._inside_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._inside_json_ld:
            self._json_ld_parts.append(data)
        else:
            self.text.append(data)


def is_absolute_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate_required_files(errors: list[str]) -> None:
    for filename in ("index.html", "robots.txt", "sitemap.xml", "llms.txt"):
        if not (DOCS / filename).is_file():
            errors.append(f"Missing required file: docs/{filename}")


def validate_html(errors: list[str]) -> tuple[DocumentParser, dict[str, object]]:
    parser = DocumentParser()
    parser.feed((DOCS / "index.html").read_text(encoding="utf-8"))

    if parser.canonicals != [CANONICAL_URL]:
        errors.append("index.html must contain exactly one canonical link to https://ethenj.com/.")

    for key, expected_value in REQUIRED_META.items():
        actual_value = parser.meta.get(key, "")
        label = f'{key[0]}="{key[1]}"'
        if not actual_value:
            errors.append(f"Missing or empty metadata: {label}")
        elif expected_value is not None and actual_value != expected_value:
            errors.append(f"Metadata {label} must equal {expected_value!r}; found {actual_value!r}")

    for asset in ("assets/profile.jpg", "assets/social-preview.jpg"):
        if not (DOCS / asset).is_file():
            errors.append(f"Missing metadata asset: docs/{asset}")

    if len(parser.json_ld_blocks) != 1:
        errors.append("index.html must contain exactly one JSON-LD script block.")
        return parser, {}

    try:
        data = json.loads(parser.json_ld_blocks[0])
    except json.JSONDecodeError as error:
        errors.append(f"JSON-LD is not valid JSON: {error}")
        return parser, {}

    return parser, data


def validate_json_ld(errors: list[str], parser: DocumentParser, data: dict[str, object]) -> None:
    graph = data.get("@graph") if isinstance(data, dict) else None
    if not isinstance(graph, list):
        errors.append("JSON-LD must contain an @graph array.")
        return

    nodes = [node for node in graph if isinstance(node, dict)]
    profile_pages = [node for node in nodes if node.get("@type") == "ProfilePage"]
    people = [node for node in nodes if node.get("@type") == "Person"]
    articles = [node for node in nodes if node.get("@type") == "ScholarlyArticle"]

    if len(profile_pages) != 1:
        errors.append("JSON-LD must contain exactly one ProfilePage.")
    if len(people) != 1:
        errors.append("JSON-LD must contain exactly one primary Person.")
    if len(articles) != 3:
        errors.append("JSON-LD must contain exactly three ScholarlyArticle nodes.")

    if not people:
        return

    person = people[0]
    expected_person = {
        "name": "Yicheng Jiang",
        "url": CANONICAL_URL,
        "image": "https://ethenj.com/assets/profile.jpg",
    }
    for key, expected_value in expected_person.items():
        if person.get(key) != expected_value:
            errors.append(f"Primary Person {key} must equal {expected_value!r}.")

    same_as = person.get("sameAs")
    if not isinstance(same_as, list) or {SCHOLAR_URL, GITHUB_URL} - set(same_as):
        errors.append("Primary Person sameAs must include the visible Google Scholar and GitHub URLs.")

    visible_text = " ".join(parser.text)
    structured_urls = set()
    for article in articles:
        headline = article.get("headline")
        url = article.get("url")
        authors = article.get("author")
        if not isinstance(headline, str) or headline not in visible_text:
            errors.append(f"Structured publication headline is missing from visible content: {headline!r}")
        if not isinstance(url, str) or url not in parser.links:
            errors.append(f"Structured publication URL is not a visible publication link: {url!r}")
        else:
            structured_urls.add(url)
        if not isinstance(authors, list) or not authors:
            errors.append(f"Structured publication has no authors: {headline!r}")
            continue
        for author in authors:
            name = author.get("name") if isinstance(author, dict) else None
            if isinstance(author, dict) and author.get("@id") == person.get("@id"):
                name = person.get("name")
            if not isinstance(name, str) or name not in visible_text:
                errors.append(f"Structured publication author is missing from visible content: {name!r}")

    if structured_urls != PUBLICATION_URLS:
        errors.append("Structured publication URLs must exactly match the three visible Paper/DOI links.")


def validate_robots(errors: list[str]) -> None:
    robots = (DOCS / "robots.txt").read_text(encoding="utf-8")
    required_lines = {
        "User-agent: *",
        "Content-Signal: search=yes, ai-input=yes, ai-train=yes",
        "Allow: /",
        f"Sitemap: {CANONICAL_URL}sitemap.xml",
    }
    missing = required_lines - set(robots.splitlines())
    if missing:
        errors.append(f"robots.txt is missing required lines: {', '.join(sorted(missing))}")


def validate_sitemap(errors: list[str]) -> None:
    try:
        root = ElementTree.parse(DOCS / "sitemap.xml").getroot()
    except ElementTree.ParseError as error:
        errors.append(f"sitemap.xml is not valid XML: {error}")
        return

    if root.tag != f"{{{SITEMAP_NAMESPACE}}}urlset":
        errors.append("sitemap.xml must use the standard sitemap namespace.")
        return

    locations = [element.text for element in root.findall(f"{{{SITEMAP_NAMESPACE}}}url/{{{SITEMAP_NAMESPACE}}}loc")]
    if locations != [CANONICAL_URL]:
        errors.append("sitemap.xml must contain exactly one loc: https://ethenj.com/.")
    elif not is_absolute_https_url(locations[0]):
        errors.append("sitemap.xml loc must be an absolute HTTPS URL.")


def validate_llms(errors: list[str]) -> None:
    content = (DOCS / "llms.txt").read_text(encoding="utf-8")
    if not content.startswith("# Yicheng Jiang\n"):
        errors.append("llms.txt must begin with '# Yicheng Jiang'.")
    required_urls = {CANONICAL_URL, *PUBLICATION_URLS}
    for url in required_urls:
        if url not in content:
            errors.append(f"llms.txt is missing required URL: {url}")


def main() -> int:
    errors: list[str] = []
    validate_required_files(errors)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    parser, data = validate_html(errors)
    validate_json_ld(errors, parser, data)
    validate_robots(errors)
    validate_sitemap(errors)
    validate_llms(errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    print("Static site metadata and discovery artifacts are valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
