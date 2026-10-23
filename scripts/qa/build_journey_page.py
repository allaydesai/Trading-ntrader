"""Build the interactive E2E research journey page from its markdown plan.

The markdown (`docs/qa/e2e-research-journey-plan.md`) is the only place the plan is
written. This script parses it into data, injects that into the page template, and
writes the HTML that is published as a Superset page. Results recorded on the page
live in the page's own storage, keyed by test-case id, so a rebuild never loses them
as long as an id is not renamed.

    uv run python -m scripts.qa.build_journey_page            # build only
    uv run python -m scripts.qa.build_journey_page --publish  # build, then publish
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = ROOT / "docs/qa/e2e-research-journey-plan.md"
TEMPLATE_PATH = Path(__file__).with_name("journey_page.html")
OUT_PATH = ROOT / "docs/qa/e2e-research-journey-plan.html"

_MD = MarkdownIt("commonmark").enable("table")
_SECTION = re.compile(r"^## (?:\d+\.\s*)?(.+)$")
_STAGE = re.compile(r"^### (S\d+) — (.+)$")
_CASE = re.compile(r"^\*\*(TC-[\w-]+) · (.+?)\*\*\s*(.*)$")
_ROW = re.compile(r"^- ([^:]+?):\s*(.*)$")
_SKILL = re.compile(r"^- Skill\?\s*(yes|no)", re.IGNORECASE)
_QUESTION = re.compile(r"^(\d+)\. \*\*(.+?)\*\* — (.+)$")


def _inline(text: str) -> str:
    return _MD.renderInline(text.strip())


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def _split(lines: list[str], pattern: re.Pattern[str]) -> tuple[list[str], list[tuple]]:
    """Lines before the first match, then (match, body lines) per match."""
    head: list[str] = []
    parts: list[tuple] = []
    for line in lines:
        m = pattern.match(line)
        if m:
            parts.append((m, []))
        elif parts:
            parts[-1][1].append(line)
        else:
            head.append(line)
    return head, parts


def _new_case(m: re.Match[str]) -> dict[str, Any]:
    rest = m.group(3).strip()
    note = rest[2:-2] if rest.startswith("*(") and rest.endswith(")*") else ""
    track = re.search(r"Track ([AB])", note)
    case: dict[str, Any] = {
        "id": m.group(1),
        "title": m.group(2).strip(),
        "note": note,
        "rth": "RTH" in note,
        "track": track.group(1) if track else None,
        "skill": False,
        "optional": "(optional)" in m.group(2).lower(),
        "rows": [],
    }
    if rest.startswith("—"):
        case["rows"].append(["Summary", rest.lstrip("— ")])
    return case


def _parse_case(m: re.Match[str], body: list[str]) -> dict[str, Any]:
    case = _new_case(m)
    for line in body:
        skill, row = _SKILL.match(line), _ROW.match(line)
        if skill:
            case["skill"] = skill.group(1).lower() == "yes"
        elif row:
            case["rows"].append([row.group(1), row.group(2)])
        elif line.strip() and case["rows"]:
            case["rows"][-1][1] += " " + line.strip()
    case["rows"] = [[label, _inline(text)] for label, text in case["rows"]]
    return case


def _parse_stages(lines: list[str]) -> tuple[str, list[dict]]:
    intro, parts = _split(lines, _STAGE)
    stages = []
    for m, body in parts:
        stage_intro, cases = _split(body, _CASE)
        stages.append(
            {
                "id": m.group(1),
                "name": m.group(2).strip(),
                "intro": _MD.render("\n".join(stage_intro)),
                "cases": [_parse_case(cm, cb) for cm, cb in cases],
            }
        )
    return _MD.render("\n".join(intro)), stages


def _parse_questions(lines: list[str]) -> list[dict]:
    questions: list[dict] = []
    for line in lines:
        m = _QUESTION.match(line)
        if m:
            questions.append({"id": f"q{m.group(1)}", "title": m.group(2), "text": m.group(3)})
        elif line.strip() and questions:
            questions[-1]["text"] += " " + line.strip()
    return [{**q, "text": _inline(q["text"])} for q in questions]


def parse_plan(text: str) -> dict:
    """Parse the plan's markdown into the data the page renders."""
    lines = text.splitlines()
    title = next(line[2:] for line in lines if line.startswith("# "))
    head, parts = _split(lines, _SECTION)
    byline = next((ln.strip() for ln in head if ln.strip() and not ln.startswith("#")), "")
    plan: dict = {"title": title.split(" — ")[0].strip(), "byline": byline}
    plan.update(sections=[], stages=[], cases_intro="", questions=[])
    part = "before"
    for m, body in parts:
        name = m.group(1).strip()
        if name == "Test cases":
            plan["cases_intro"], plan["stages"] = _parse_stages(body)
            part = "after"
        elif name.startswith("Open questions"):
            plan["questions"] = _parse_questions(body)
        else:
            section = {"id": _slug(name), "title": name, "part": part}
            plan["sections"].append({**section, "html": _MD.render("\n".join(body))})
    return plan


def build_page(markdown: str, template: str) -> str:
    """Return the page HTML: the template with the parsed plan injected."""
    plan = parse_plan(markdown)
    data = json.dumps(plan, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("__TITLE__", html.escape(plan["title"])).replace("__PLAN_JSON__", data)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the E2E research journey page.")
    parser.add_argument("--publish", action="store_true", help="Publish as a Superset page.")
    parser.add_argument("--label", default="rebuilt from the markdown plan")
    args = parser.parse_args()
    page = build_page(PLAN_PATH.read_text(), TEMPLATE_PATH.read_text())
    OUT_PATH.write_text(page)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}")
    if args.publish:
        title = parse_plan(PLAN_PATH.read_text())["title"]
        cmd = ["superset", "pages", "publish", str(OUT_PATH.relative_to(ROOT))]
        cmd += ["--title", title, "--label", args.label, "--visibility", "just_me"]
        raise SystemExit(subprocess.run(cmd, cwd=ROOT).returncode)


if __name__ == "__main__":
    main()
