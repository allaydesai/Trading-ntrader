"""The QA journey page is built from its markdown plan, so the plan is written once."""

from pathlib import Path

from scripts.qa.build_journey_page import build_page, parse_plan

PLAN = """# E2E Research Journey — QA Plan

2026-10-21 · Owner: Allay

## 1. Why this exists

Nobody has walked **one** strategy through it.

## 5. Test cases

Each case is a step.

### S0 — Preflight

**TC-S0-01 · Services are up**
- Steps: run `alembic upgrade head`.
- Expected: single head.
- Learn: why it matters.
- Skill? yes — preflight.

**TC-S0-05 · Broker path works** *(RTH day only)*
- Steps: `live check`.
- Expected: exit 0.

### S8 — Compare live against the backtest

See section 6.

**TC-S8-03 · L3 distribution** — `get_session` against the band, weekly.

**TC-S8-04 · Weekly check** *(Track A, every week)*
- Steps: read it.

## 11. Open questions for Allay

1. **Track B instrument** — NVDA or an ETF?
2. **Gate override** — agree to it?
"""


class TestParsePlan:
    def test_reads_title_and_byline(self):
        plan = parse_plan(PLAN)
        assert plan["title"] == "E2E Research Journey"
        assert plan["byline"] == "2026-10-21 · Owner: Allay"

    def test_prose_sections_render_as_html_and_keep_order(self):
        plan = parse_plan(PLAN)
        assert [s["title"] for s in plan["sections"]] == ["Why this exists"]
        assert "<strong>one</strong>" in plan["sections"][0]["html"]

    def test_stages_hold_their_cases_with_labelled_rows(self):
        stage = parse_plan(PLAN)["stages"][0]
        assert (stage["id"], stage["name"]) == ("S0", "Preflight")
        case = stage["cases"][0]
        assert case["id"] == "TC-S0-01"
        assert case["title"] == "Services are up"
        assert case["rows"][0] == ["Steps", "run <code>alembic upgrade head</code>."]
        assert case["skill"] is True
        assert [r[0] for r in case["rows"]] == ["Steps", "Expected", "Learn"]

    def test_note_sets_rth_and_track_flags(self):
        stages = parse_plan(PLAN)["stages"]
        rth = stages[0]["cases"][1]
        assert rth["rth"] is True and rth["track"] is None
        weekly = stages[1]["cases"][1]
        assert weekly["track"] == "A" and weekly["rth"] is False

    def test_one_line_case_keeps_its_description(self):
        case = parse_plan(PLAN)["stages"][1]["cases"][0]
        assert case["id"] == "TC-S8-03"
        assert case["rows"] == [["Summary", "<code>get_session</code> against the band, weekly."]]

    def test_stage_intro_is_kept(self):
        assert "See section 6." in parse_plan(PLAN)["stages"][1]["intro"]

    def test_open_questions_are_numbered_answer_slots(self):
        questions = parse_plan(PLAN)["questions"]
        assert questions[0] == {
            "id": "q1",
            "title": "Track B instrument",
            "text": "NVDA or an ETF?",
        }
        assert [q["id"] for q in questions] == ["q1", "q2"]


def test_build_page_injects_plan_into_template(tmp_path: Path):
    template = tmp_path / "t.html"
    template.write_text("<title>__TITLE__</title><script>const PLAN = __PLAN_JSON__;</script>")
    html = build_page(PLAN, template.read_text())
    assert "<title>E2E Research Journey</title>" in html
    assert '"TC-S0-01"' in html
    assert "__PLAN_JSON__" not in html


def test_json_cannot_close_the_script_tag():
    html = build_page(PLAN.replace("NVDA or an ETF?", "</script>?"), "__PLAN_JSON__")
    assert "</script>" not in html
