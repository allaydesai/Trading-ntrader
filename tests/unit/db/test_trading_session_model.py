"""AC #1, #2, #7 guard (Story 2.2): the TradingSession ORM shape, no database needed.

Verifies the ``values_callable`` guard directly: without it SQLAlchemy persists the
enum member *name* (``CREATED``) rather than its *value* (``created``), which is the
single highest-risk line in this story (see the story's Dev Notes).
"""

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from src.db.models.trade import Trade
from src.db.models.trading_session import TradingSession


@pytest.mark.unit
def test_tablename_is_trading_sessions():
    assert TradingSession.__tablename__ == "trading_sessions"


@pytest.mark.unit
def test_status_enum_labels_are_lowercase_values_not_member_names():
    """The values_callable guard: enum labels must be the lowercase values."""
    status_column = TradingSession.__table__.columns["status"]
    assert isinstance(status_column.type, sa.Enum)
    assert status_column.type.enums == ["created", "running", "stopped", "sealed"]
    assert status_column.type.name == "session_status"


@pytest.mark.unit
def test_status_column_defaults_to_created_and_is_not_nullable():
    status_column = TradingSession.__table__.columns["status"]
    assert status_column.nullable is False
    assert status_column.default is not None


@pytest.mark.unit
def test_spec_column_is_jsonb_and_not_nullable():
    spec_column = TradingSession.__table__.columns["spec"]
    assert isinstance(spec_column.type, JSONB)
    assert spec_column.nullable is False


@pytest.mark.unit
def test_id_is_bigint_primary_key():
    id_column = TradingSession.__table__.columns["id"]
    assert isinstance(id_column.type, sa.BigInteger)
    assert id_column.primary_key is True


@pytest.mark.unit
def test_session_id_is_unique_indexed_uuid():
    session_id_column = TradingSession.__table__.columns["session_id"]
    assert session_id_column.unique is True
    assert session_id_column.index is True
    assert session_id_column.nullable is False


@pytest.mark.unit
def test_name_is_unique_indexed_and_not_nullable():
    name_column = TradingSession.__table__.columns["name"]
    assert name_column.unique is True
    assert name_column.index is True
    assert name_column.nullable is False


@pytest.mark.unit
def test_linked_backtest_run_id_is_nullable_fk_to_backtest_runs_run_id():
    column = TradingSession.__table__.columns["linked_backtest_run_id"]
    assert column.nullable is True
    fk_targets = {fk.target_fullname for fk in column.foreign_keys}
    assert "backtest_runs.run_id" in fk_targets


@pytest.mark.unit
def test_sealed_run_id_is_nullable_with_no_foreign_key():
    column = TradingSession.__table__.columns["sealed_run_id"]
    assert column.nullable is True
    assert len(column.foreign_keys) == 0


@pytest.mark.unit
def test_five_nullable_timestamp_columns_exist():
    for name in (
        "last_started_at",
        "last_stopped_at",
        "sealed_at",
        "last_heartbeat_at",
        "last_bar_at",
    ):
        column = TradingSession.__table__.columns[name]
        assert column.nullable is True, f"{name} must be nullable"


@pytest.mark.unit
def test_created_at_present_via_timestamp_mixin():
    assert "created_at" in TradingSession.__table__.columns
    assert "updated_at" not in TradingSession.__table__.columns


@pytest.mark.unit
def test_trading_session_has_no_backtest_run_id_column():
    """Guards against confusing TradingSession's linked_backtest_run_id with Trade's."""
    assert "backtest_run_id" not in TradingSession.__table__.columns


@pytest.mark.unit
def test_status_index_exists_for_story_28():
    index_names = {index.name for index in TradingSession.__table__.indexes}
    assert "ix_trading_sessions_status" in index_names


@pytest.mark.unit
def test_trade_backtest_run_id_is_now_nullable():
    """AC #3: trades.backtest_run_id widens to nullable."""
    column = Trade.__table__.columns["backtest_run_id"]
    assert column.nullable is True


@pytest.mark.unit
def test_trade_session_id_column_exists_and_targets_trading_sessions_id():
    """trades.session_id targets trading_sessions.id (BigInteger), not the UUID key."""
    column = Trade.__table__.columns["session_id"]
    assert column.nullable is True
    fk_targets = {fk.target_fullname for fk in column.foreign_keys}
    assert "trading_sessions.id" in fk_targets


@pytest.mark.unit
def test_trade_owner_check_constraint_present():
    check_names = {c.name for c in Trade.__table_args__ if isinstance(c, sa.CheckConstraint)}
    assert "chk_trades_owner" in check_names
