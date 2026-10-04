"""Unit tests for run provenance: git state and config hash (MCP spec S2.3)."""

import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.models.backtest_request import BacktestRequest
from src.models.run_provenance import RunProvenance
from src.services.provenance import compute_config_hash, git_provenance, run_provenance

pytestmark = pytest.mark.unit


def _request(**overrides) -> BacktestRequest:
    fields = {
        "strategy_type": "sma_crossover",
        "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
        "config_path": "src.core.strategies.sma_crossover:SMAConfig",
        "strategy_config": {"fast_period": 10, "slow_period": 20},
        "symbol": "QQQ",
        "instrument_id": "QQQ.NAMED_CATALOG",
        "start_date": datetime(2000, 1, 1, tzinfo=timezone.utc),
        "end_date": datetime(2015, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc),
        "bar_type": "1-DAY-LAST",
        "starting_balance": Decimal("1000000"),
        "catalog_name": "firstrate-etf",
    }
    fields.update(overrides)
    return BacktestRequest(**fields)


class TestComputeConfigHash:
    def test_is_a_sha256_hex_digest(self):
        digest = compute_config_hash(_request())
        assert len(digest) == 64
        int(digest, 16)

    def test_is_deterministic(self):
        assert compute_config_hash(_request()) == compute_config_hash(_request())

    def test_ignores_param_insertion_order(self):
        a = _request(strategy_config={"fast_period": 10, "slow_period": 20})
        b = _request(strategy_config={"slow_period": 20, "fast_period": 10})
        assert compute_config_hash(a) == compute_config_hash(b)

    def test_ignores_decimal_trailing_zeros(self):
        a = _request(starting_balance=Decimal("1000000"))
        b = _request(starting_balance=Decimal("1000000.00"))
        assert compute_config_hash(a) == compute_config_hash(b)

    def test_ignores_persist_flag_and_config_file_path(self):
        a = _request(persist=True, config_file_path=None)
        b = _request(persist=False, config_file_path="/tmp/x.yaml")
        assert compute_config_hash(a) == compute_config_hash(b)

    @pytest.mark.parametrize(
        "override",
        [
            {"strategy_config": {"fast_period": 11, "slow_period": 20}},
            {"symbol": "SPY", "instrument_id": "SPY.NAMED_CATALOG"},
            {"bar_type": "1-HOUR-LAST"},
            {"start_date": datetime(2001, 1, 1, tzinfo=timezone.utc)},
            {"end_date": datetime(2014, 12, 31, tzinfo=timezone.utc)},
            {"starting_balance": Decimal("500000")},
            {"catalog_name": "firstrate-stocks"},
            {"strategy_path": "src.core.strategies.sma_momentum:SMAMomentum"},
            {"fill_seed": 7},
        ],
    )
    def test_changes_when_a_run_defining_field_changes(self, override):
        assert compute_config_hash(_request(**override)) != compute_config_hash(_request())

    def test_handles_decimal_params(self):
        req = _request(strategy_config={"position_size_pct": Decimal("10.0")})
        same = _request(strategy_config={"position_size_pct": Decimal("10")})
        assert compute_config_hash(req) == compute_config_hash(same)


class TestGitProvenance:
    def test_reads_this_repository(self):
        repo = Path(__file__).resolve().parents[3]
        info = git_provenance(repo)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
        ).stdout.strip()
        assert info.git_commit == head
        assert isinstance(info.git_dirty, bool)

    def test_outside_a_repository_returns_nones(self, tmp_path):
        info = git_provenance(tmp_path)
        assert info.git_commit is None
        assert info.git_dirty is None
        assert info.strategies_commit is None

    def test_detects_a_dirty_tree(self, tmp_path):
        def git(*args):
            subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

        git("init", "-q")
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
        assert git_provenance(tmp_path).git_dirty is False
        (tmp_path / "new.txt").write_text("x")
        assert git_provenance(tmp_path).git_dirty is True


def test_run_provenance_combines_git_and_hash(tmp_path):
    prov = run_provenance(_request(), repo_root=tmp_path)
    assert isinstance(prov, RunProvenance)
    assert prov.config_hash == compute_config_hash(_request())
    assert prov.git_commit is None
