#!/usr/bin/env python3
"""Dependency-free structural mobile UI gate for the Airwise browser pages.

THIS IS NOT A RENDERED VISUAL TEST. Browser screenshots and manual interactions
are a separate mandatory gate as described in airwise-visual-qa/SKILL.md.
"""
from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "home_air_quality_alexa"
SCREENS = {
    "dashboard.html": "/",
    "pollution.html": "/pollution",
    "context.html": "/context",
}
SKILLS = (
    "airwise-design-direction",
    "airwise-mobile-first",
    "airwise-data-viz",
    "airwise-visual-qa",
)


class Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.canvas: list[dict[str, str]] = []
        self.tables = 0
        self.viewport = False
        self.language = False
        self.script_external = False
        self.stylesheet_external = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {k: v or "" for k, v in attrs}
        if tag == "html" and attributes.get("lang") == "it":
            self.language = True
        if tag == "meta" and attributes.get("name") == "viewport":
            self.viewport = True
        if tag == "a":
            self.links.append(attributes)
        if tag == "canvas":
            self.canvas.append(attributes)
        if tag == "table":
            self.tables += 1
        if tag == "script" and attributes.get("src"):
            self.script_external = True
        if tag == "link" and attributes.get("rel") == "stylesheet":
            self.stylesheet_external = True


def scan_repo(root: Path = ROOT) -> list[str]:
    failures: list[str] = []
    if not (root / "DESIGN.md").is_file() or not (root / "AGENTS.md").is_file():
        failures.append("Missing DESIGN.md or AGENTS.md")
    for name in SKILLS:
        path = root / ".agents" / "skills" / name / "SKILL.md"
        if not path.is_file():
            failures.append(f"Missing Agent Skill {path}")
            continue
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or f"name: {name}" not in text or "\ndescription:" not in text:
            failures.append(f"Invalid SKILL.md front matter: {name}")
        if len(text) < 400:
            failures.append(f"Skill appears incomplete: {name}")

    for filename, active in SCREENS.items():
        path = root / "src" / "home_air_quality_alexa" / filename
        if not path.is_file():
            failures.append(f"Missing UI page: {filename}")
            continue
        html = path.read_text(encoding="utf-8")
        parser = Scan()
        parser.feed(html)
        if not parser.language or not parser.viewport:
            failures.append(f"{filename}: missing Italian lang or mobile viewport")
        if parser.script_external or parser.stylesheet_external:
            failures.append(f"{filename}: remote scripts or styles are not allowed")
        for dest in SCREENS.values():
            if not any(link.get("href") == dest and "aw-nav" in html for link in parser.links):
                failures.append(f"{filename}: missing destination {dest}")
        if not any(link.get("href") == active and link.get("aria-current") == "page" for link in parser.links):
            failures.append(f"{filename}: missing active navigation state")
        mandatory = ("--aq-touch-target:44px", "safe-area-inset-bottom",
                     "min-height:44px", "aw-data-details", "aw-table-scroll",
                     "focus-visible")
        for token in mandatory:
            if token not in html:
                failures.append(f"{filename}: missing mobile token {token}")
        if parser.canvas and (parser.tables < 1 or "onpointerdown" not in html):
            failures.append(f"{filename}: canvas needs table alternative and tap interaction")
        if not re.search(r"@media\(max-width:(?:760|870|860)px\)", html):
            failures.append(f"{filename}: no narrow-screen media rule found")
        if filename == "pollution.html":
            if 'class="aw-heatmap-scroll"' not in html or "min-width:600px" not in html:
                failures.append("pollution.html: dense heatmap must scroll locally")
        if filename == "context.html":
            if ".form input,.form select,select{font-size:16px!important" not in html:
                failures.append("context.html: form input size must avoid mobile auto-zoom")
        if "<script src=" in html or "localStorage" in html:
            failures.append(f"{filename}: unexpected client dependency/storage")
    return failures


if __name__ == "__main__":
    faults = scan_repo()
    if faults:
        for fault in faults:
            print(f"FAIL mobile-structural: {fault}")
        raise SystemExit(1)
    print(f"PASS mobile-structural: {len(SCREENS)} pages; {len(SKILLS)} original Agent Skills; accessibility/mobile tokens.")
    print("NOTE: structural pass only; verify 320/375/390/768/1024/1440 rendered viewports separately.")
