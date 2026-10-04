"""The paper loop on real runs (F4, S6.2, S7.1-S7.3): commands for a tested candidate, then a
session read against the band built from its out-of-sample run.

The study's runs are real (worker, e2e-test catalog). The paper session is a row
inserted the way ``live create`` writes one, with closed trades as the trade
recorder writes them: the server only ever reads it.
"""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.db.models.trade import Trade
from src.db.models.trading_session import TradingSession
from src.mcp_server.errors import ToolFailure
from src.mcp_server.paper import handoff, reads
from src.mcp_server.paper.record import export_session
from src.mcp_server.paper.sessions import get_session, list_sessions
from src.mcp_server.paper.specs import model_normaliser
from src.mcp_server.studies import candidates, scorecard
from src.mcp_server.studies.submit import reserve_trial
from src.models.session import SessionStatus
from tests.integration.mcp_server.conftest import requires_e2e_data
from tests.integration.mcp_server.study_fixtures import GATES_BLOCK, coverage, make_ctx, open_study
from tests.integration.mcp_server.test_honest_loop import _finish, _in_sample

pytestmark = [pytest.mark.integration, requires_e2e_data]

PAPER_GATES = GATES_BLOCK.replace("```\n", "G4:\n  min_weeks: 8\n  min_trades: 20\n```\n")
NOW = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
STUDY = "sma-aapl"


@pytest.fixture
def ctx(tmp_path, monkeypatch, isolated_results):
    ctx = make_ctx(tmp_path, monkeypatch, gates=PAPER_GATES)
    monkeypatch.setattr(handoff, "catalog_availability", coverage)
    open_study(ctx, symbols=["AAPL"], out_of_sample_end=date(2020, 12, 31))
    ctx.sessions = isolated_results
    return ctx


async def _out_of_sample(ctx, reason: str | None = None) -> str:
    job, _ = candidates.prepare_out_of_sample(STUDY, reason)
    return await _finish(ctx, reserve_trial(ctx.runner, job))


@pytest.fixture
async def tested(ctx):
    """A frozen candidate with one completed out-of-sample run; returns that run's id."""
    candidates.freeze_candidate(ctx.runner.store, STUDY, await _in_sample(ctx), None)
    return await _out_of_sample(ctx)


def _seed_session(ctx, *, compare_to: str, params: dict | None = None, trades: int = 6) -> int:
    """A running session as live create and the trade recorder would leave it."""
    card = scorecard.get_scorecard(ctx.settings, ctx.runner.store, STUDY, None)
    frozen = card["candidate"]["params"]
    spec_params = model_normaliser("sma_crossover")(params or frozen)
    with ctx.sessions.begin() as db:
        row = TradingSession(
            name="sma-aapl-v1-paper",
            status=SessionStatus.RUNNING,
            spec={
                "schema_version": 1,
                "strategies": [
                    {
                        "strategy_id": "sma_crossover",
                        "parameters": spec_params,
                        "bar_types": ["AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"],
                    }
                ],
            },
            linked_backtest_run_id=UUID(compare_to),
            last_started_at=NOW - timedelta(weeks=3),
            last_heartbeat_at=NOW - timedelta(seconds=5),
            last_bar_at=NOW - timedelta(hours=10),
        )
        db.add(row)
        db.flush()
        for i in range(trades):
            entry = NOW - timedelta(weeks=3) + timedelta(days=3 * i + 1)
            db.add(
                Trade(
                    session_id=row.id,
                    instrument_id="AAPL.NASDAQ",
                    trade_id=f"T-{i}",
                    venue_order_id=f"V-{i}",
                    client_order_id=f"C-{i}",
                    order_side="BUY",
                    quantity=Decimal("100"),
                    entry_price=Decimal("200"),
                    exit_price=Decimal("202") if i % 2 else Decimal("199"),
                    profit_loss=Decimal("199") if i % 2 else Decimal("-101"),
                    commission_amount=Decimal("1"),
                    entry_timestamp=entry,
                    exit_timestamp=entry + timedelta(days=2),
                )
            )
        return row.id


def _code(fn, *args, **kwargs) -> str:
    with pytest.raises(ToolFailure) as exc:
        fn(*args, **kwargs)
    return exc.value.code


async def test_commands_wait_for_the_out_of_sample_run(ctx):
    candidates.freeze_candidate(ctx.runner.store, STUDY, await _in_sample(ctx), None)
    code = _code(handoff.paper_commands, ctx.settings, ctx.runner.store, STUDY, None, None)
    assert code == "no_out_of_sample_run"


async def test_commands_link_the_session_to_the_out_of_sample_run(ctx, tested):
    out = handoff.paper_commands(ctx.settings, ctx.runner.store, STUDY, None, None)
    assert out["compare_to"] == tested
    assert out["session_name"] == "sma-aapl-v1-paper"
    assert out["bar_type"] == "AAPL.NASDAQ-1-DAY-LAST-EXTERNAL"
    create = next(c["command"] for c in out["commands"] if c["step"] == "create")
    assert create.endswith(f"--compare-to {tested}")
    for key in out["params"]:
        assert f"--param {key}=" in create
    assert out["expectation_band"]["source_run"] == tested
    assert any(w.startswith("Gates ") for w in out["warnings"])
    assert out["already_linked"] == []
    taken = _code(handoff.paper_commands, ctx.settings, ctx.runner.store, STUDY, None, "")
    assert taken == "invalid_session_name"


async def test_a_session_is_read_against_its_band_and_feeds_g4(ctx, tested):
    _seed_session(ctx, compare_to=tested)
    report = get_session(ctx.settings, "sma-aapl-v1-paper", now=NOW)
    assert report["link"]["study"] == STUDY and report["link"]["is_latest_out_of_sample"]
    assert report["spec_check"] == {"matches": True, "differences": []}
    assert report["horizon"]["weeks"] == pytest.approx(3.0)
    assert report["horizon"]["start"].endswith("+00:00")
    assert report["paper"]["trades"] == 6 and report["paper"]["win_rate"] == 0.5
    assert report["band"]["source_run"] == tested
    assert report["band"]["time_windows"]["days"] == 21
    assert report["drift"]["slippage"]["measurable"] is False
    assert report["g4"] == {
        "min_weeks": 8,
        "min_trades": 20,
        "weeks": 3.0,
        "trades": 6,
        "reached": False,
    }
    assert len(report["recent_trades"]) == 6

    listed = list_sessions(study=STUDY, now=NOW)
    assert listed["total"] == 1
    row = listed["sessions"][0]
    assert (row["alive"], row["closed_trades"], row["version"]) == (True, 6, 1)
    assert list_sessions(study="another-study", now=NOW)["total"] == 0
    assert _code(list_sessions, status="paused") == "invalid_status"

    card = scorecard.get_scorecard(ctx.settings, ctx.runner.store, STUDY, None, band_weeks=4)
    assert card["evidence"]["paper_session"] == "sma-aapl-v1-paper"
    g4 = card["gates"]["G4"]
    assert g4["status"] == "missing"
    assert "6 of 20" in g4["checks"]["min_trades"]["note"]
    assert card["expectation_band"]["horizon"] == {"weeks": 4.0, "trades": 20}
    again = handoff.paper_commands(ctx.settings, ctx.runner.store, STUDY, None, None)
    assert again["session_name"] == "sma-aapl-v1-paper-2"
    assert again["already_linked"] == ["sma-aapl-v1-paper"]


async def test_a_session_trading_other_params_is_a_config_flag(ctx, tested):
    _seed_session(ctx, compare_to=tested, params={"fast_period": 7, "slow_period": 20})
    report = get_session(ctx.settings, "sma-aapl-v1-paper", now=NOW)
    assert not report["spec_check"]["matches"]
    assert any("fast_period" in d for d in report["spec_check"]["differences"])
    kinds = {f["kind"]: f["cause"] for f in report["drift"]["flags"]}
    assert kinds["spec_mismatch"] == "config"
    # Six trades in three weeks is also far more than this run's band: two causes.
    causes = set(kinds.values()) - {"strategy_or_regime"}
    expected = "config" if causes == {"config"} else "mixed"
    assert report["drift"]["likely_cause"] == expected


async def test_a_session_on_an_overridden_first_look_still_links(ctx, tested):
    await _out_of_sample(ctx, "vendor fixed the 2020 bars")
    _seed_session(ctx, compare_to=tested)
    report = get_session(ctx.settings, "sma-aapl-v1-paper", now=NOW)
    assert report["link"]["study"] == STUDY
    assert report["link"]["is_latest_out_of_sample"] is False
    assert any("newest out-of-sample" in w for w in report["warnings"])
    card = scorecard.get_scorecard(ctx.settings, ctx.runner.store, STUDY, None)
    assert card["evidence"]["paper_session"] == "sma-aapl-v1-paper"


def test_the_read_only_transaction_refuses_a_write(isolated_results):
    with pytest.raises(DBAPIError, match="read-only"):
        with reads.read_only_session() as db:
            db.execute(text("CREATE TABLE should_not_exist (id int)"))


def test_an_unknown_session_says_where_to_look(ctx):
    assert _code(get_session, ctx.settings, "nope") == "unknown_session"


async def test_a_session_exports_beside_its_compare_to_run(ctx, tested):
    _seed_session(ctx, compare_to=tested)
    out = export_session(
        ctx.settings, "sma-aapl-v1-paper", slug="paper-check", folder="Lab/results", overwrite=False
    )
    assert out["session_trades"] == 6 and out["runs"] == 1
    lines = open(out["files"]["session_trades_csv"]).read().splitlines()
    assert len(lines) == 7 and lines[0].startswith("instrument_id,")
    record = json.loads(open(out["files"]["session"]).read())
    assert record["kind"] == "paper-session" and len(record["trades"]) == 6
    assert record["link"]["compare_to"] == tested
    with pytest.raises(ToolFailure):
        export_session(
            ctx.settings,
            "sma-aapl-v1-paper",
            slug="paper-check",
            folder="Lab/results",
            overwrite=False,
        )
