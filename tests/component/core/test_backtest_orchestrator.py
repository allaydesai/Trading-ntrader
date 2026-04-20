"""Component tests for multi-instrument BacktestOrchestrator (Story 3.1, Task 5)."""

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


def _make_request(symbol: str = "AAPL") -> MagicMock:
    """Build a lightweight mock BacktestRequest."""
    request = MagicMock()
    request.strategy_type = "sma_crossover"
    request.strategy_path = "src.core.strategies.sma_crossover:SMACrossover"
    request.config_path = None
    request.strategy_config = {}
    request.symbol = symbol
    request.instrument_id = f"{symbol}.NASDAQ"
    request.start_date = datetime(2018, 1, 1, tzinfo=timezone.utc)
    request.end_date = datetime(2018, 6, 30, tzinfo=timezone.utc)
    request.bar_type = "1-DAY-LAST"
    request.persist = False
    request.starting_balance = Decimal("1000000")
    request.data_source = "catalog"
    request.catalog_name = "e2e-test"
    return request


def _make_instrument(symbol: str, venue: str):
    inst = MagicMock()
    inst.id = MagicMock()
    inst.id.venue = MagicMock()
    inst.id.venue.__str__ = lambda self, v=venue: v  # type: ignore[assignment]
    inst.id.symbol = MagicMock()
    inst.id.symbol.value = symbol
    return inst


def _make_bar(instrument_id_str: str):
    bar = MagicMock()
    bar.bar_type.instrument_id = MagicMock()
    bar.bar_type.instrument_id.__str__ = (
        lambda self, s=instrument_id_str: s  # type: ignore[assignment]
    )
    return bar


@pytest.mark.component
class TestExecuteMulti:
    """Tests for BacktestOrchestrator.execute_multi."""

    @pytest.mark.asyncio
    async def test_multi_instrument_execute_adds_venues_instruments_and_data(self):
        """execute_multi: one add_venue per unique venue, then instruments, data, strategy."""
        from src.core.backtest_orchestrator import BacktestOrchestrator

        orchestrator = BacktestOrchestrator()

        inst_aapl = _make_instrument("AAPL", "NASDAQ")
        inst_msft = _make_instrument("MSFT", "NASDAQ")  # same venue
        bars_aapl = [_make_bar("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL")]
        bars_msft = [_make_bar("MSFT.NASDAQ-1-DAY-LAST-EXTERNAL")]
        request = _make_request(symbol="AAPL")

        mock_engine = MagicMock()
        mock_strategy = MagicMock()
        mock_extractor_result = MagicMock()

        with (
            patch("src.core.backtest_orchestrator.BacktestEngine", return_value=mock_engine),
            patch.object(orchestrator, "_create_strategy_multi", return_value=mock_strategy),
            patch.object(orchestrator, "_extract_results", return_value=mock_extractor_result),
        ):
            result, run_id = await orchestrator.execute_multi(
                request,
                instrument_bars=[(inst_aapl, bars_aapl), (inst_msft, bars_msft)],
            )

        # Only one venue for NASDAQ
        assert mock_engine.add_venue.call_count == 1
        assert mock_engine.add_instrument.call_count == 2
        assert mock_engine.add_data.call_count == 2
        mock_engine.add_strategy.assert_called_once_with(mock_strategy)
        mock_engine.run.assert_called_once()
        assert result is mock_extractor_result
        assert run_id is None  # persist=False

    @pytest.mark.asyncio
    async def test_multi_instrument_execute_multiple_venues(self):
        """Two instruments on different venues must each get add_venue."""
        from src.core.backtest_orchestrator import BacktestOrchestrator

        orchestrator = BacktestOrchestrator()

        inst_a = _make_instrument("AAPL", "NASDAQ")
        inst_b = _make_instrument("SPY", "ARCA")
        bars_a = [_make_bar("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL")]
        bars_b = [_make_bar("SPY.ARCA-1-DAY-LAST-EXTERNAL")]
        request = _make_request()

        mock_engine = MagicMock()

        with (
            patch("src.core.backtest_orchestrator.BacktestEngine", return_value=mock_engine),
            patch.object(orchestrator, "_create_strategy_multi", return_value=MagicMock()),
            patch.object(orchestrator, "_extract_results", return_value=MagicMock()),
        ):
            await orchestrator.execute_multi(
                request, instrument_bars=[(inst_a, bars_a), (inst_b, bars_b)]
            )

        venues_added = {
            call.kwargs.get("venue") or (call.args[0] if call.args else None)
            for call in mock_engine.add_venue.call_args_list
        }
        assert len(venues_added) == 2

    @pytest.mark.asyncio
    async def test_multi_instrument_execute_raises_on_empty_pairs(self):
        """execute_multi must reject an empty instrument_bars list."""
        from src.core.backtest_orchestrator import BacktestOrchestrator

        orchestrator = BacktestOrchestrator()
        request = _make_request()

        with pytest.raises(ValueError, match="No instrument_bars provided"):
            await orchestrator.execute_multi(request, instrument_bars=[])

    @pytest.mark.asyncio
    async def test_multi_instrument_execute_dispose_on_failure(self):
        """Setup failure mid-pair must still allow orchestrator.dispose() to succeed."""
        from src.core.backtest_orchestrator import BacktestOrchestrator

        orchestrator = BacktestOrchestrator()

        inst_a = _make_instrument("AAPL", "NASDAQ")
        inst_b = _make_instrument("SPY", "ARCA")
        bars_a = [_make_bar("AAPL.NASDAQ-1-DAY-LAST-EXTERNAL")]
        bars_b = [_make_bar("SPY.ARCA-1-DAY-LAST-EXTERNAL")]
        request = _make_request()

        mock_engine = MagicMock()
        mock_engine.add_instrument.side_effect = [None, RuntimeError("simulated mid-setup fault")]

        with (
            patch("src.core.backtest_orchestrator.BacktestEngine", return_value=mock_engine),
            patch.object(orchestrator, "_create_strategy_multi", return_value=MagicMock()),
        ):
            with pytest.raises(RuntimeError, match="simulated mid-setup fault"):
                await orchestrator.execute_multi(
                    request, instrument_bars=[(inst_a, bars_a), (inst_b, bars_b)]
                )

        # Dispose must not raise even though engine setup was interrupted
        orchestrator.dispose()
        mock_engine.dispose.assert_called_once()
