"""The existing config-snapshot panel renders a sealed record's conditions (Story 5.4, AC #6).

AR7's promise is that ``session_conditions`` needs no UI change: it is ordinary
JSONB in an existing column, and the detail page's configuration panel renders
every top-level ``config_snapshot`` key. This renders the **real**
``templates/partials/config_snapshot.html`` through the UI's own
``Jinja2Templates`` and the real presentation model (``build_configuration``)
over a sealed record's snapshot, and reads the conditions back out of the HTML.
Both files are read, never written: AR44 keeps ``src/api/**`` and
``templates/**`` zero-diff, and Story 5.6 owns the full view verification.
"""

import html
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.api.models.backtest_detail import build_configuration
from src.api.ui.backtests import templates
from src.models.reconciliation import ViewPosition
from src.services.session_conditions import AR7_KEYS, build_session_conditions

pytestmark = pytest.mark.unit

SOURCE = "_bmad-output/planning-artifacts/prd-epic5-scope.md"
PANEL = "partials/config_snapshot.html"


def _sealed_run() -> SimpleNamespace:
    """The columns ``build_configuration`` reads, as a sealed record stores them."""
    conditions = build_session_conditions(
        starting_capital=Decimal("100000.00"),
        closed_trade_count=4,
        open_positions=(ViewPosition("AAPL.NASDAQ", Decimal("10")),),
        last_run_flags=None,
    )
    conditions["equity_series"] = {"granularity": "closed_trade", "closed_trades": 4}
    return SimpleNamespace(
        instrument_symbol="AAPL",
        data_source="ibkr_live",
        start_date=datetime(2026, 9, 1, 13, 0, tzinfo=UTC),
        end_date=datetime(2026, 10, 9, 20, 0, tzinfo=UTC),
        initial_capital=Decimal("100000.00"),
        strategy_name="Sma Crossover",
        strategy_type="sma_crossover",
        config_snapshot={
            "strategy_path": "src.core.strategies.sma_crossover:SMACrossover",
            "config_path": "src.core.strategies.sma_crossover:SMAConfig",
            "version": "1.0",
            "config": {"fast_period": 10},
            "bar_type": "1-MINUTE-LAST",
            "session_conditions": conditions,
        },
    )


def _render(run: SimpleNamespace) -> str:
    view = SimpleNamespace(configuration=build_configuration(run))
    return html.unescape(templates.env.get_template(PANEL).render(view=view))


class TestTheExistingPanelRendersTheConditions:
    @pytest.mark.acceptance_criterion(
        "5.4f",
        "the existing config-snapshot panel displays session_conditions without any UI change",
        SOURCE,
    )
    def test_every_condition_appears_in_the_unmodified_panel(self):
        run = _sealed_run()
        conditions = run.config_snapshot["session_conditions"]

        rendered = _render(run)

        assert "Session Conditions" in rendered
        # `{{ value }}` renders the mapping's text; every AR7 key and value is in it.
        assert str(conditions) in rendered
        for key in AR7_KEYS:
            assert f"'{key}': {conditions[key]!r}" in rendered

    def test_the_run_rows_own_columns_render_beside_them(self):
        rendered = _render(_sealed_run())

        assert "ibkr_live" in rendered
        assert "$100,000.00" in rendered
        assert "2026-09-01 to 2026-10-09" in rendered

    def test_the_panel_rendered_is_the_real_file(self):
        """A copy of the template would prove nothing about the one served."""
        template = templates.env.get_template(PANEL)

        assert template.filename is not None
        assert template.filename.endswith("templates/partials/config_snapshot.html")
