"""Vault export: runner-compatible files, written only inside allowed folders (S8.1)."""

import csv
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.mcp_server.errors import ToolFailure
from src.mcp_server.export import export_target, trade_stats, write_export
from src.mcp_server.settings import McpSettings

pytestmark = pytest.mark.unit

UTC = timezone.utc


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "Lab" / "results").mkdir(parents=True)
    return tmp_path


def _settings(vault, **kw):
    return McpSettings(_env_file=None, vault_path=vault, **kw)


def _trade(year, pnl, days=2.0, commission=1.0):
    exit_ts = datetime(year, 6, 1, tzinfo=UTC)
    return {
        "profit_loss": pnl,
        "exit_timestamp": exit_ts,
        "entry_timestamp": exit_ts - timedelta(days=days),
        "holding_period_seconds": int(days * 86400),
        "commission_amount": commission,
    }


class TestTradeStats:
    RUN = {
        "start_date": datetime(2020, 1, 1, tzinfo=UTC),
        "end_date": datetime(2021, 12, 31, tzinfo=UTC),
    }

    def test_no_closed_trades(self):
        assert trade_stats([{"profit_loss": None, "exit_timestamp": None}], self.RUN) == {
            "closed_trades": 0
        }

    def test_stats(self):
        trades = [_trade(2020, 100.0), _trade(2020, -30.0), _trade(2021, -50.0, days=4)]
        stats = trade_stats(trades, self.RUN)
        assert stats["closed_trades"] == 3
        assert stats["pnl_by_exit_year"] == {"2020": 70.0, "2021": -50.0}
        assert stats["losing_years"] == ["2021"]
        assert stats["avg_hold_days"] == pytest.approx(2.67, abs=0.01)
        assert stats["max_hold_days"] == 4.0
        assert stats["top5_trades_share_of_pnl_pct"] == 100.0  # total 20, top5 = all
        assert stats["total_commission"] == 3.0
        assert stats["exposure_pct"] == pytest.approx(100 * 8 / 729.999, abs=0.05)


class TestExportTarget:
    def test_resolves_inside_the_allowed_folder(self, vault):
        target = export_target(_settings(vault), "Lab/results", "rsi2-qqq", overwrite=False)
        assert target.json_path == vault / "Lab" / "results" / "rsi2-qqq.json"
        assert target.csv_path == vault / "Lab" / "results" / "rsi2-qqq.trades.csv"

    def test_vault_not_configured(self):
        with pytest.raises(ToolFailure) as exc:
            export_target(McpSettings(_env_file=None), "Lab/results", "x", overwrite=False)
        assert exc.value.code == "vault_not_configured"

    def test_folder_must_be_allowed(self, vault):
        (vault / "Strategies").mkdir()
        with pytest.raises(ToolFailure) as exc:
            export_target(_settings(vault), "Strategies", "x", overwrite=False)
        assert exc.value.code == "folder_not_allowed"
        assert "Lab/results" in exc.value.fix

    @pytest.mark.parametrize("slug", ["../escape", "a/b", "UPPER", "", ".hidden", "x" * 90])
    def test_slug_must_be_safe(self, vault, slug):
        with pytest.raises(ToolFailure) as exc:
            export_target(_settings(vault), "Lab/results", slug, overwrite=False)
        assert exc.value.code == "invalid_slug"

    def test_symlinked_folder_escaping_the_vault_is_refused(self, vault, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        (vault / "Lab" / "evil").symlink_to(outside)
        settings = _settings(vault, export_folders=["Lab/evil"])
        with pytest.raises(ToolFailure) as exc:
            export_target(settings, "Lab/evil", "x", overwrite=False)
        assert exc.value.code == "folder_not_allowed"

    def test_missing_folder(self, vault):
        settings = _settings(vault, export_folders=["Lab/nope"])
        with pytest.raises(ToolFailure) as exc:
            export_target(settings, "Lab/nope", "x", overwrite=False)
        assert exc.value.code == "folder_missing"

    def test_existing_file_needs_overwrite(self, vault):
        (vault / "Lab" / "results" / "x.json").write_text("{}")
        with pytest.raises(ToolFailure) as exc:
            export_target(_settings(vault), "Lab/results", "x", overwrite=False)
        assert exc.value.code == "file_exists"
        assert export_target(_settings(vault), "Lab/results", "x", overwrite=True)


def test_write_export_creates_runner_format_files(vault):
    target = export_target(_settings(vault), "Lab/results", "demo", overwrite=False)
    runs = [{"run_id": "r1", "metrics": {"total_return": 0.1}, "trade_stats": {"closed_trades": 1}}]
    trades = [
        {"run_id": "r1", "profit_loss": 5.0, "exit_timestamp": datetime(2020, 1, 2, tzinfo=UTC)}
    ]
    files = write_export(
        target, slug="demo", runs=runs, trades=trades, missing=["r2"], git={"commit": "abc"}
    )

    payload = json.loads(target.json_path.read_text())
    assert payload["job"] == "demo"
    assert payload["kind"] == "export-runs"
    assert payload["status"] == "ok"
    assert payload["git"] == {"commit": "abc"}
    assert payload["runs"] == runs
    assert payload["missing"] == ["r2"]
    assert payload["trades_csv"] == "demo.trades.csv"
    rows = list(csv.DictReader(target.csv_path.open()))
    assert rows[0]["run_id"] == "r1"
    assert files == {"json": str(target.json_path), "trades_csv": str(target.csv_path)}


def test_write_export_without_trades_writes_no_csv(vault):
    target = export_target(_settings(vault), "Lab/results", "empty", overwrite=False)
    files = write_export(target, slug="empty", runs=[], trades=[], missing=[], git={})
    assert files["trades_csv"] is None
    assert not target.csv_path.exists()
