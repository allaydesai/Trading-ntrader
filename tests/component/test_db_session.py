"""Tests for database session management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import (
    dispose_all_connections,
    get_async_engine,
    get_async_session_maker,
    get_db,
    get_session,
)
from src.db.session import (
    test_connection as db_test_connection,
)


class TestDatabaseSession:
    """Test cases for database session management."""

    @pytest.fixture(autouse=True)
    def cleanup_session_state(self):
        """Clean up global session state after each test to prevent pollution."""
        import src.db.session

        # Store original state
        original_async_engine = src.db.session.async_engine
        original_async_session_local = src.db.session.AsyncSessionLocal

        yield  # Test runs here

        # Restore original state after test
        src.db.session.async_engine = original_async_engine
        src.db.session.AsyncSessionLocal = original_async_session_local

    @patch("src.db.session.settings")
    @pytest.mark.component
    def test_get_async_engine_first_call(self, mock_settings):
        """Test get_async_engine creates engine on first call."""
        mock_settings.database_url = "postgresql://user:pass@localhost/test"
        mock_settings.database_pool_size = 5
        mock_settings.database_max_overflow = 10
        mock_settings.database_pool_timeout = 30

        # Reset global state
        import src.db.session

        src.db.session.async_engine = None

        with patch("src.db.session.create_async_engine") as mock_create:
            mock_engine = MagicMock()
            mock_create.return_value = mock_engine

            engine = get_async_engine()

            assert engine == mock_engine
            mock_create.assert_called_once_with(
                "postgresql+asyncpg://user:pass@localhost/test",
                pool_size=5,
                max_overflow=10,
                pool_timeout=30,
                pool_pre_ping=True,
                pool_recycle=3600,
            )

    @patch("src.db.session.settings")
    @pytest.mark.component
    def test_get_async_engine_no_database_url(self, mock_settings):
        """Test get_async_engine when no database URL is configured."""
        mock_settings.database_url = None

        # Reset global state
        import src.db.session

        src.db.session.async_engine = None

        engine = get_async_engine()
        assert engine is None

    @patch("src.db.session.settings")
    @pytest.mark.component
    def test_get_async_engine_cached(self, mock_settings):
        """Test get_async_engine returns cached engine on subsequent calls."""
        mock_settings.database_url = "postgresql://user:pass@localhost/test"

        # Set up cached engine
        import src.db.session

        cached_engine = MagicMock()
        src.db.session.async_engine = cached_engine

        with patch("src.db.session.create_async_engine") as mock_create:
            engine = get_async_engine()

            assert engine == cached_engine
            mock_create.assert_not_called()

    @pytest.mark.component
    def test_get_async_session_maker_with_engine(self):
        """Test get_async_session_maker with valid engine."""
        # Reset global state
        import src.db.session

        src.db.session.AsyncSessionLocal = None

        mock_engine = MagicMock()

        with patch("src.db.session.get_async_engine", return_value=mock_engine):
            with patch("src.db.session.async_sessionmaker") as mock_sessionmaker:
                mock_session_maker = MagicMock()
                mock_sessionmaker.return_value = mock_session_maker

                session_maker = get_async_session_maker()

                assert session_maker == mock_session_maker
                mock_sessionmaker.assert_called_once_with(
                    mock_engine, class_=AsyncSession, expire_on_commit=False
                )

    @pytest.mark.component
    def test_get_async_session_maker_no_engine(self):
        """Test get_async_session_maker when no engine available."""
        # Reset global state
        import src.db.session

        src.db.session.AsyncSessionLocal = None

        with patch("src.db.session.get_async_engine", return_value=None):
            session_maker = get_async_session_maker()
            assert session_maker is None

    @pytest.mark.component
    def test_get_async_session_maker_cached(self):
        """Test get_async_session_maker returns cached session maker."""
        # Set up cached session maker
        import src.db.session

        cached_session_maker = MagicMock()
        src.db.session.AsyncSessionLocal = cached_session_maker

        with patch("src.db.session.get_async_engine") as mock_get_engine:
            session_maker = get_async_session_maker()

            assert session_maker == cached_session_maker
            mock_get_engine.assert_not_called()

    @patch("src.db.session.SessionLocal")
    @pytest.mark.component
    def test_get_db_success(self, mock_session_local):
        """Test get_db successfully yields database session."""
        mock_session = MagicMock()
        mock_session_local.return_value = mock_session

        # Test the generator
        db_gen = get_db()

        # Get the session
        db_session = next(db_gen)
        assert db_session == mock_session

        # Complete the generator (simulates finally block)
        try:
            next(db_gen)
        except StopIteration:
            pass

        # Verify session was closed
        mock_session.close.assert_called_once()

    @patch("src.db.session.SessionLocal", None)
    @pytest.mark.component
    def test_get_db_no_session_local(self):
        """Test get_db raises error when SessionLocal is None."""
        with pytest.raises(RuntimeError, match="Database not configured"):
            next(get_db())

    @pytest.mark.asyncio
    async def test_get_session_success(self):
        """Test get_session successfully yields async database session."""
        mock_session = AsyncMock()
        mock_session_context = AsyncMock()
        mock_session_context.__aenter__.return_value = mock_session
        mock_session_context.__aexit__.return_value = None

        # Mock the session maker to return a context manager
        mock_session_maker = MagicMock()
        mock_session_maker.return_value = mock_session_context

        with patch("src.db.session.get_async_session_maker", return_value=mock_session_maker):
            async with get_session() as session:
                assert session == mock_session

            # Verify session maker was called
            mock_session_maker.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_session_no_session_maker(self):
        """Test get_session raises error when no session maker available."""
        with patch("src.db.session.get_async_session_maker", return_value=None):
            with pytest.raises(RuntimeError, match="Database not configured"):
                async with get_session():
                    pass

    @pytest.mark.asyncio
    @patch("src.db.session.settings")
    async def test_test_connection_success(self, mock_settings):
        """Test test_connection with successful database connection."""
        mock_settings.database_url = "postgresql://user:pass@localhost/test"

        # Mock the connection with execute and close methods
        mock_connection = AsyncMock()
        mock_connection.execute = AsyncMock()
        mock_connection.close = AsyncMock()

        mock_engine = AsyncMock()
        mock_engine.connect = AsyncMock(return_value=mock_connection)

        with patch("src.db.session.get_async_engine", return_value=mock_engine):
            result = await db_test_connection()

            assert result is True
            mock_engine.connect.assert_called_once()
            mock_connection.execute.assert_called_once()
            mock_connection.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.db.session.settings")
    async def test_test_connection_no_database_url(self, mock_settings):
        """Test test_connection when no database URL configured."""
        mock_settings.database_url = None

        result = await db_test_connection()
        assert result is False

    @pytest.mark.asyncio
    @patch("src.db.session.settings")
    async def test_test_connection_no_engine(self, mock_settings):
        """Test test_connection when no engine available."""
        mock_settings.database_url = "postgresql://user:pass@localhost/test"

        with patch("src.db.session.get_async_engine", return_value=None):
            result = await db_test_connection()
            assert result is False

    @pytest.mark.asyncio
    @patch("src.db.session.settings")
    async def test_test_connection_exception(self, mock_settings):
        """Test test_connection handles exceptions."""
        mock_settings.database_url = "postgresql://user:pass@localhost/test"

        mock_engine = AsyncMock()
        mock_engine.connect.side_effect = Exception("Connection failed")

        with patch("src.db.session.get_async_engine", return_value=mock_engine):
            with patch("logging.error") as mock_logging:
                result = await db_test_connection()

                assert result is False
                mock_logging.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispose_all_connections_with_engine(self):
        """Test dispose_all_connections when engine exists."""
        # Set up global state with mock engine
        import src.db.session

        mock_engine = AsyncMock()
        src.db.session.async_engine = mock_engine
        src.db.session.AsyncSessionLocal = MagicMock()

        await dispose_all_connections()

        # Verify engine was disposed and globals reset
        mock_engine.dispose.assert_called_once()
        assert src.db.session.async_engine is None
        assert src.db.session.AsyncSessionLocal is None

    @pytest.mark.asyncio
    async def test_dispose_all_connections_no_engine(self):
        """Test dispose_all_connections when no engine exists."""
        # Ensure global state is clean
        import src.db.session

        src.db.session.async_engine = None
        src.db.session.AsyncSessionLocal = None

        # Should not raise any exceptions
        await dispose_all_connections()

        # Global state should remain None
        assert src.db.session.async_engine is None
        assert src.db.session.AsyncSessionLocal is None

    @patch("src.db.session.settings")
    @pytest.mark.component
    def test_url_conversion_postgresql_to_asyncpg(self, mock_settings):
        """Test that postgresql:// URLs are converted to postgresql+asyncpg://."""
        mock_settings.database_url = "postgresql://user:pass@localhost/test"
        mock_settings.database_pool_size = 5
        mock_settings.database_max_overflow = 10
        mock_settings.database_pool_timeout = 30

        # Reset global state
        import src.db.session

        src.db.session.async_engine = None

        with patch("src.db.session.create_async_engine") as mock_create:
            get_async_engine()

            # Verify URL was converted
            call_args = mock_create.call_args[0]
            assert call_args[0] == "postgresql+asyncpg://user:pass@localhost/test"

    @pytest.mark.component
    def test_get_db_generator_cleanup(self):
        """Test that get_db properly handles session cleanup."""
        mock_session = MagicMock()

        with patch("src.db.session.SessionLocal", return_value=mock_session):
            db_gen = get_db()

            # Test normal flow
            session = next(db_gen)
            assert session == mock_session

            # Test cleanup - simulate generator completion
            try:
                next(db_gen)
            except StopIteration:
                pass

            # Verify session was closed
            mock_session.close.assert_called_once()
