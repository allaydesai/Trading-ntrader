"""Unit tests for explorer-to-backtest bridge URL helpers (Story 3.2).

Covers:
- `_build_run_backtest_url` — URL construction from explorer state + instrument metadata.
- `_build_explorer_return` — inner explorer URL that round-trips back from the run.
- `TIMEFRAME_EXPLORER_TO_RUN_FORM` / `TIMEFRAME_RUN_FORM_TO_EXPLORER` mappings.
- `_resolve_back_to_explorer_url` — guarded "Back to Explorer" URL on the detail page.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from src.api.models.explorer import ExplorerTimeframe
from src.api.ui.backtests import _resolve_back_to_explorer_url
from src.api.ui.explorer import (
    TIMEFRAME_EXPLORER_TO_RUN_FORM,
    TIMEFRAME_RUN_FORM_TO_EXPLORER,
    _build_explorer_return,
    _build_run_backtest_url,
)


@pytest.mark.unit
class TestTimeframeMapping:
    """Explorer label ↔ run-form timeframe mapping is the single source of truth."""

    def test_maps_all_five_explorer_labels(self):
        assert TIMEFRAME_EXPLORER_TO_RUN_FORM == {
            "D": "1-DAY",
            "1H": "1-HOUR",
            "30m": "30-MINUTE",
            "5m": "5-MINUTE",
            "1m": "1-MINUTE",
        }

    def test_reverse_map_is_exact_inverse(self):
        assert TIMEFRAME_RUN_FORM_TO_EXPLORER == {
            "1-DAY": "D",
            "1-HOUR": "1H",
            "30-MINUTE": "30m",
            "5-MINUTE": "5m",
            "1-MINUTE": "1m",
        }

    @pytest.mark.parametrize(
        "run_form_tf,explorer_label",
        [
            ("1-DAY", "D"),
            ("1-HOUR", "1H"),
            ("30-MINUTE", "30m"),
            ("5-MINUTE", "5m"),
            ("1-MINUTE", "1m"),
        ],
    )
    def test_reverse_timeframe_map(self, run_form_tf, explorer_label):
        assert TIMEFRAME_RUN_FORM_TO_EXPLORER[run_form_tf] == explorer_label

    @pytest.mark.parametrize("unmapped", ["4-HOUR", "1-WEEK", "15-MINUTE"])
    def test_reverse_timeframe_map_unmapped_returns_none(self, unmapped):
        assert TIMEFRAME_RUN_FORM_TO_EXPLORER.get(unmapped) is None


@pytest.mark.unit
class TestBuildRunBacktestUrl:
    """`_build_run_backtest_url` produces the bridge URL for the Run Backtest anchor."""

    def _params(self, url):
        """Parse a URL's query string into a simple dict."""
        parsed = urlparse(url)
        assert parsed.path == "/backtests/run"
        return {k: v[0] for k, v in parse_qs(parsed.query).items()}

    def test_build_run_backtest_url_daily(self):
        url = _build_run_backtest_url(
            catalog="firstrate-research",
            ticker="SPY",
            active_tf=ExplorerTimeframe.DAILY,
            date_range_start=datetime(2003, 1, 2, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_count=5500,
            explorer_state=None,
        )
        params = self._params(url)
        assert params["catalog"] == "firstrate-research"
        assert params["ticker"] == "SPY"
        assert params["timeframe"] == "1-DAY"
        assert params["start"] == "2003-01-02"
        assert params["end"] == "2024-12-31"

    def test_build_run_backtest_url_hourly(self):
        url = _build_run_backtest_url(
            catalog="c",
            ticker="AAPL",
            active_tf=ExplorerTimeframe.HOURLY,
            date_range_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_count=500,
            explorer_state=None,
        )
        assert self._params(url)["timeframe"] == "1-HOUR"

    def test_build_run_backtest_url_30min(self):
        """30m must resolve in the bridge map — no KeyError/500 when 30m bars exist (Story 2.1)."""
        url = _build_run_backtest_url(
            catalog="c",
            ticker="AAPL",
            active_tf=ExplorerTimeframe.THIRTY_MIN,
            date_range_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_count=500,
            explorer_state=None,
        )
        assert self._params(url)["timeframe"] == "30-MINUTE"

    def test_build_run_backtest_url_5min(self):
        url = _build_run_backtest_url(
            catalog="c",
            ticker="AAPL",
            active_tf=ExplorerTimeframe.FIVE_MIN,
            date_range_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_count=500,
            explorer_state=None,
        )
        assert self._params(url)["timeframe"] == "5-MINUTE"

    def test_build_run_backtest_url_1min(self):
        url = _build_run_backtest_url(
            catalog="c",
            ticker="AAPL",
            active_tf=ExplorerTimeframe.ONE_MIN,
            date_range_start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_count=500,
            explorer_state=None,
        )
        assert self._params(url)["timeframe"] == "1-MINUTE"

    def test_build_run_backtest_url_missing_date_range(self):
        url = _build_run_backtest_url(
            catalog="c",
            ticker="SPY",
            active_tf=ExplorerTimeframe.DAILY,
            date_range_start=None,
            date_range_end=None,
            bar_count=100,
            explorer_state=None,
        )
        params = self._params(url)
        assert "start" not in params
        assert "end" not in params
        assert params["catalog"] == "c"
        assert params["ticker"] == "SPY"
        assert params["timeframe"] == "1-DAY"

    def test_build_run_backtest_url_partial_date_range(self):
        """Only one side of the range populated — emit the one that exists."""
        url = _build_run_backtest_url(
            catalog="c",
            ticker="SPY",
            active_tf=ExplorerTimeframe.DAILY,
            date_range_start=datetime(2003, 1, 2, tzinfo=timezone.utc),
            date_range_end=None,
            bar_count=100,
            explorer_state=None,
        )
        params = self._params(url)
        assert params["start"] == "2003-01-02"
        assert "end" not in params

    def test_build_run_backtest_url_zero_bars(self):
        url = _build_run_backtest_url(
            catalog="c",
            ticker="SPY",
            active_tf=ExplorerTimeframe.DAILY,
            date_range_start=datetime(2003, 1, 2, tzinfo=timezone.utc),
            date_range_end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            bar_count=0,
            explorer_state=None,
        )
        assert url is None

    def test_build_run_backtest_url_includes_explorer_return(self):
        url = _build_run_backtest_url(
            catalog="foo",
            ticker="SPY",
            active_tf=ExplorerTimeframe.DAILY,
            date_range_start=None,
            date_range_end=None,
            bar_count=100,
            explorer_state={
                "search": "SP",
                "asset_class": "ETF",
                "sort_by": "ticker",
                "page": 2,
            },
        )
        params = self._params(url)
        # explorer_return is a URL-encoded /explorer?... URL
        assert "explorer_return" in params
        decoded = unquote(params["explorer_return"])
        assert decoded.startswith("/explorer?")
        inner_params = {k: v[0] for k, v in parse_qs(decoded.split("?", 1)[1]).items()}
        assert inner_params["catalog"] == "foo"
        assert inner_params["ticker"] == "SPY"
        assert inner_params["tf"] == "D"
        assert inner_params["search"] == "SP"
        assert inner_params["asset_class"] == "ETF"
        assert inner_params["sort_by"] == "ticker"
        assert inner_params["page"] == "2"
        # Full URL should be comfortably under browser-safe limit.
        assert len(url) < 2000

    def test_build_run_backtest_url_ticker_with_dot(self):
        """Tickers like `BRK.B` must survive URL encoding and round-trip parse."""
        url = _build_run_backtest_url(
            catalog="c",
            ticker="BRK.B",
            active_tf=ExplorerTimeframe.DAILY,
            date_range_start=None,
            date_range_end=None,
            bar_count=100,
            explorer_state=None,
        )
        assert "ticker=BRK.B" in url
        assert self._params(url)["ticker"] == "BRK.B"


@pytest.mark.unit
class TestBuildExplorerReturn:
    """`_build_explorer_return` builds the inner /explorer?... URL for round-trip."""

    def test_minimal_state(self):
        url = _build_explorer_return(
            catalog="c",
            ticker="SPY",
            active_tf=ExplorerTimeframe.DAILY,
            state={},
        )
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert params == {"catalog": "c", "ticker": "SPY", "tf": "D"}

    def test_full_state(self):
        url = _build_explorer_return(
            catalog="c",
            ticker="SPY",
            active_tf=ExplorerTimeframe.HOURLY,
            state={
                "search": "SP",
                "asset_class": "ETF",
                "sort_by": "ticker",
                "page": 3,
            },
        )
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert params["catalog"] == "c"
        assert params["ticker"] == "SPY"
        assert params["tf"] == "1H"
        assert params["search"] == "SP"
        assert params["asset_class"] == "ETF"
        assert params["sort_by"] == "ticker"
        assert params["page"] == "3"

    def test_empty_string_values_omitted(self):
        """Empty strings should not leak into the URL as '?search='."""
        url = _build_explorer_return(
            catalog="c",
            ticker="SPY",
            active_tf=ExplorerTimeframe.DAILY,
            state={"search": "", "asset_class": "", "sort_by": "ticker", "page": 1},
        )
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert "search" not in params
        assert "asset_class" not in params
        # page=1 is the default — also omitted to keep URL clean
        assert "page" not in params


def _mock_run(**kwargs):
    """Build a minimal object that exposes `.catalog_name`, `.symbol`, `.timeframe`."""
    defaults = {
        "catalog_name": "c",
        "symbol": "SPY",
        "timeframe": "1-DAY",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


@pytest.mark.unit
class TestResolveBackToExplorerUrl:
    """`_resolve_back_to_explorer_url` — open-redirect guard + deterministic fallback."""

    def test_valid_explorer_return_preserved(self):
        url = _resolve_back_to_explorer_url(
            "/explorer?catalog=foo&ticker=SPY",
            _mock_run(),
        )
        assert url == "/explorer?catalog=foo&ticker=SPY"

    def test_exact_slash_explorer_preserved(self):
        url = _resolve_back_to_explorer_url("/explorer", _mock_run())
        assert url == "/explorer"

    def test_url_encoded_explorer_return_decoded_and_preserved(self):
        url = _resolve_back_to_explorer_url(
            "%2Fexplorer%3Fcatalog%3Dfoo",
            _mock_run(),
        )
        assert url == "/explorer?catalog=foo"

    @pytest.mark.parametrize(
        "bad",
        [
            "https://evil.example.com/phish",
            "//evil.example.com/path",
            "javascript:alert(1)",
            "data:text/html,<x>",
            "../backtests",
            "/explorers/evil",  # prefix-startswith check must reject "explorers"
            "/exp",
            "",
        ],
    )
    def test_malicious_explorer_return_falls_back(self, bad):
        url = _resolve_back_to_explorer_url(bad, _mock_run(timeframe="1-DAY"))
        assert "evil" not in url
        assert "javascript" not in url
        assert url.startswith("/explorer")

    def test_fallback_with_catalog_name_and_known_timeframe(self):
        url = _resolve_back_to_explorer_url(None, _mock_run(timeframe="1-HOUR"))
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert params["catalog"] == "c"
        assert params["ticker"] == "SPY"
        assert params["tf"] == "1H"

    def test_fallback_without_catalog_name(self):
        url = _resolve_back_to_explorer_url(None, _mock_run(catalog_name=None))
        assert url == "/explorer"

    def test_fallback_unmapped_timeframe_omits_tf(self):
        url = _resolve_back_to_explorer_url(None, _mock_run(timeframe="4-HOUR"))
        params = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert "tf" not in params
        assert params["catalog"] == "c"
        assert params["ticker"] == "SPY"
