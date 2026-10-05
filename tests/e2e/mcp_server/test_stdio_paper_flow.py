"""Scenario steps 12-14 over stdio (research MCP phase-3 exit check, minus the broker).

A study is tested out-of-sample -> paper_commands gives the commands, linked to that
run -> a session row as `live create` and the trade recorder would leave it (the
server never creates one) -> list_sessions and get_session read it against its band
-> the scorecard shows G4 in progress -> export_results(session=...) files it in the
vault. Real server, real workers, real e2e-test catalog and Postgres; every study,
run and session is deleted afterwards.
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from mcp import Client

from src.db.models.trade import Trade
from src.db.models.trading_session import TradingSession
from src.db.session_sync import get_sync_session
from src.mcp_server.paper.specs import model_normaliser
from src.models.session import SessionStatus
from tests.e2e.mcp_server.conftest import GATES_MD, call, requires_e2e_data
from tests.e2e.mcp_server.test_stdio_study_flow import _run

pytestmark = [pytest.mark.e2e, requires_e2e_data]

G4 = "G4:\n  min_weeks: 8\n  min_trades: 20\n```\n"


def _insert_session(name: str, compare_to: str, params: dict, bar_type: str) -> None:
    now = datetime.now(timezone.utc)
    with get_sync_session() as session:
        row = TradingSession(
            name=name,
            status=SessionStatus.RUNNING,
            created_at=now - timedelta(weeks=2),
            spec={
                "schema_version": 1,
                "strategies": [
                    {
                        "strategy_id": "sma_crossover",
                        "parameters": model_normaliser("sma_crossover")(params),
                        "bar_types": [bar_type],
                    }
                ],
            },
            linked_backtest_run_id=UUID(compare_to),
            last_started_at=now - timedelta(weeks=2),
            last_heartbeat_at=now,
            last_bar_at=now - timedelta(hours=1),
        )
        session.add(row)
        session.flush()
        entry = now - timedelta(days=10)
        session.add(
            Trade(
                session_id=row.id,
                instrument_id="AAPL.NASDAQ",
                trade_id="E2E-1",
                venue_order_id="E2E-V1",
                client_order_id="E2E-C1",
                order_side="BUY",
                quantity=Decimal("10"),
                entry_price=Decimal("200"),
                exit_price=Decimal("204"),
                profit_loss=Decimal("39"),
                commission_amount=Decimal("1"),
                entry_timestamp=entry,
                exit_timestamp=entry + timedelta(days=3),
            )
        )


async def test_a_tested_candidate_goes_to_paper_and_is_read_back(
    server_params, vault, created_runs, created_studies, created_sessions
):
    (vault / "System" / "Gates.md").write_text(GATES_MD.rsplit("```", 1)[0] + G4)
    slug = f"e2e-paper-{uuid4().hex[:8]}"
    created_studies.append(slug)
    study = {"study": slug}
    async with Client(server_params) as client:
        opened = await call(
            client,
            "create_study",
            {
                "slug": slug,
                "title": "SMA crossover on AAPL, to paper",
                "hypothesis": "Trends persist on large caps",
                "strategy": "sma_crossover",
                "symbols": ["AAPL"],
                "in_sample_start": "2018-01-01",
                "in_sample_end": "2019-12-31",
                "out_of_sample_start": "2020-01-01",
                "out_of_sample_end": "2020-12-31",
                "trial_budget": 2,
            },
        )
        assert opened["ok"], opened
        assert "G4" in opened["study"]["pass_criteria"]
        run_id = await _run(client, "submit_backtest", {**study, "params": {"fast_period": 5}})
        early = await call(client, "paper_commands", study)
        assert early["error"]["code"] == "no_candidate"
        assert (await call(client, "freeze_candidate", {**study, "run_id": run_id}))["ok"]
        refused = await call(client, "paper_commands", study)
        assert refused["error"]["code"] == "no_out_of_sample_run"
        oos_id = await _run(client, "run_out_of_sample", study)
        created_runs.extend([run_id, oos_id])

        commands = await call(client, "paper_commands", study)
        assert commands["ok"], commands
        assert commands["compare_to"] == oos_id
        assert commands["bar_type"] == "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"
        assert f"--compare-to {oos_id}" in commands["script"]
        name = commands["session_name"]
        assert name == f"{slug}-v1-paper"

        created_sessions.append(name)
        _insert_session(name, oos_id, commands["params"], commands["bar_type"])

        listed = await call(client, "list_sessions", study)
        assert [s["name"] for s in listed["sessions"]] == [name]
        assert listed["sessions"][0]["alive"] is True
        report = await call(client, "get_session", {"session": name})
        assert report["ok"], report
        assert report["link"]["study"] == slug and report["spec_check"]["matches"]
        assert report["paper"]["trades"] == 1
        assert report["band"]["source_run"] == oos_id
        # One trade is inside the count band; its seeded commission is far above the run's.
        assert report["drift"]["status"] == "flagged"
        assert [f["kind"] for f in report["drift"]["flags"]] == ["commission_high"]
        assert report["drift"]["likely_cause"] == "execution"
        assert report["drift"]["slippage"]["measurable"] is False

        card = await call(client, "get_scorecard", study)
        assert card["evidence"]["paper_session"] == name
        assert card["gates"]["G4"]["status"] == "missing"
        assert "1 of 20" in card["gates"]["G4"]["checks"]["min_trades"]["note"]
        assert card["expectation_band"]["source_run"] == oos_id

        exported = await call(client, "export_results", {"slug": name, "session": name})
        assert exported["ok"], exported
    session_file = Path(exported["files"]["session"])
    assert session_file.parent == (vault / "Lab" / "results").resolve()
    assert session_file.name.startswith(f"{name}.20") and session_file.name.endswith(
        ".session.json"
    )
    record = json.loads(session_file.read_text())
    assert record["kind"] == "paper-session" and record["link"]["compare_to"] == oos_id
    assert (vault / "Lab" / "results" / f"{name}.json").is_file()
