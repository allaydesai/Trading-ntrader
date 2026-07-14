"""Unit tests for InstrumentMapper — CSV parsing and instrument resolution."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.identifiers import InstrumentId

from src.db.exceptions import InstrumentMappingError
from src.db.models.catalog_instrument import CatalogInstrument
from src.services.firstrate.instrument_mapper import InstrumentMapper

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CATALOG_NAME = "firstrate-etf"
ASSET_CLASS = "ETF"


@pytest.fixture()
def mock_repo():
    """Return a mock SyncCatalogInstrumentRepository."""
    return MagicMock()


@pytest.fixture()
def mapper(mock_repo):
    """Return an InstrumentMapper with a mock repo."""
    return InstrumentMapper(repo=mock_repo)


@pytest.fixture()
def valid_csv(tmp_path: Path) -> Path:
    """Create a valid 8-column company_profiles CSV."""
    content = (
        "SPY,SPDR S&P 500 ETF Trust,US,NY,ARCA,"
        "Financial Services,Asset Management,1993-01-22\n"
        "QQQ,Invesco QQQ Trust,US,NY,NASDAQ,"
        "Financial Services,Asset Management,1999-03-10\n"
        "IWM,iShares Russell 2000 ETF,US,NY,ARCA,"
        "Financial Services,Asset Management,2000-05-22\n"
    )
    csv_file = tmp_path / "company_profiles.csv"
    csv_file.write_text(content)
    return csv_file


@pytest.fixture()
def csv_with_empty_ipo(tmp_path: Path) -> Path:
    """CSV with missing IPO date."""
    content = "XYZ,Some Fund,US,CA,BATS,Technology,Software,\n"
    csv_file = tmp_path / "profiles.csv"
    csv_file.write_text(content)
    return csv_file


@pytest.fixture()
def csv_with_blank_lines(tmp_path: Path) -> Path:
    """CSV with blank lines interspersed."""
    content = (
        "SPY,SPDR S&P 500 ETF Trust,US,NY,ARCA,"
        "Financial Services,Asset Management,1993-01-22\n"
        "\n"
        "QQQ,Invesco QQQ Trust,US,NY,NASDAQ,"
        "Financial Services,Asset Management,1999-03-10\n"
        "\n"
    )
    csv_file = tmp_path / "profiles.csv"
    csv_file.write_text(content)
    return csv_file


@pytest.fixture()
def csv_with_duplicates(tmp_path: Path) -> Path:
    """CSV with duplicate tickers — last row should win via upsert."""
    content = (
        "SPY,SPDR S&P 500 ETF Trust,US,NY,ARCA,"
        "Financial Services,Asset Management,1993-01-22\n"
        "SPY,SPDR S&P 500 Updated,US,NY,ARCA,"
        "Financial Services,Asset Management,1993-01-29\n"
    )
    csv_file = tmp_path / "profiles.csv"
    csv_file.write_text(content)
    return csv_file


@pytest.fixture()
def csv_with_header(tmp_path: Path) -> Path:
    """CSV with header row (real file format)."""
    content = (
        "Ticker,Company Name,Country,State,Exchange,"
        "Sector,Industry,Ipo Date\n"
        "SPY,SPDR S&P 500 ETF Trust,US,NY,ARCA,"
        "Financial Services,Asset Management,1993-01-22\n"
        "QQQ,Invesco QQQ Trust,US,NY,NASDAQ,"
        "Financial Services,Asset Management,1999-03-10\n"
    )
    csv_file = tmp_path / "profiles.csv"
    csv_file.write_text(content)
    return csv_file


# ---------------------------------------------------------------------------
# Task 1: CSV Parsing Tests
# ---------------------------------------------------------------------------


class TestLoadCompanyProfiles:
    """Tests for InstrumentMapper.load_company_profiles()."""

    @pytest.mark.unit
    def test_load_valid_csv_returns_row_count(self, mapper, mock_repo, valid_csv):
        """Valid CSV with 3 rows returns count of 3."""
        count = mapper.load_company_profiles(valid_csv, CATALOG_NAME, ASSET_CLASS)
        assert count == 3

    @pytest.mark.unit
    def test_load_valid_csv_calls_upsert_for_each_row(self, mapper, mock_repo, valid_csv):
        """Each CSV row results in a repo.upsert() call."""
        mapper.load_company_profiles(valid_csv, CATALOG_NAME, ASSET_CLASS)
        assert mock_repo.upsert.call_count == 3

    @pytest.mark.unit
    def test_upserted_instrument_has_correct_fields(self, mapper, mock_repo, valid_csv):
        """First upserted instrument has correct ticker, exchange, nautilus_id."""
        mapper.load_company_profiles(valid_csv, CATALOG_NAME, ASSET_CLASS)

        first_call = mock_repo.upsert.call_args_list[0]
        instrument = first_call[0][0]

        assert instrument.ticker == "SPY"
        assert instrument.exchange == "ARCA"
        assert instrument.nautilus_id == "SPY.ARCA"
        assert instrument.catalog_name == CATALOG_NAME
        assert instrument.asset_class == ASSET_CLASS
        assert instrument.name == "SPDR S&P 500 ETF Trust"
        assert instrument.sector == "Financial Services"
        assert instrument.industry == "Asset Management"
        assert instrument.ipo_date == date(1993, 1, 22)
        assert instrument.country == "US"
        assert instrument.state == "NY"

    @pytest.mark.unit
    def test_blank_country_state_parsed_as_none(self, mapper, mock_repo, tmp_path):
        """Blank country/state fields become None (not empty string)."""
        content = "ZZZ,No Domicile Co,,,NASDAQ,Technology,Software,2000-01-01\n"
        csv_file = tmp_path / "blank_domicile.csv"
        csv_file.write_text(content)

        mapper.load_company_profiles(csv_file, CATALOG_NAME, ASSET_CLASS)
        instrument = mock_repo.upsert.call_args[0][0]
        assert instrument.country is None
        assert instrument.state is None

    @pytest.mark.unit
    def test_nautilus_id_format(self, mapper, mock_repo, valid_csv):
        """nautilus_id is built as '{ticker}.{exchange}'."""
        mapper.load_company_profiles(valid_csv, CATALOG_NAME, ASSET_CLASS)

        instruments = [call[0][0] for call in mock_repo.upsert.call_args_list]
        assert instruments[0].nautilus_id == "SPY.ARCA"
        assert instruments[1].nautilus_id == "QQQ.NASDAQ"
        assert instruments[2].nautilus_id == "IWM.ARCA"

    @pytest.mark.unit
    def test_empty_ipo_date_parsed_as_none(self, mapper, mock_repo, csv_with_empty_ipo):
        """Empty IPO date field becomes None."""
        mapper.load_company_profiles(csv_with_empty_ipo, CATALOG_NAME, ASSET_CLASS)
        instrument = mock_repo.upsert.call_args[0][0]
        assert instrument.ipo_date is None

    @pytest.mark.unit
    def test_blank_lines_skipped(self, mapper, mock_repo, csv_with_blank_lines):
        """Blank lines in CSV are skipped — only data rows processed."""
        count = mapper.load_company_profiles(csv_with_blank_lines, CATALOG_NAME, ASSET_CLASS)
        assert count == 2
        assert mock_repo.upsert.call_count == 2

    @pytest.mark.unit
    def test_duplicate_tickers_both_upserted(self, mapper, mock_repo, csv_with_duplicates):
        """Duplicate tickers are both passed to upsert (DB handles dedup)."""
        count = mapper.load_company_profiles(csv_with_duplicates, CATALOG_NAME, ASSET_CLASS)
        assert count == 2
        assert mock_repo.upsert.call_count == 2

    @pytest.mark.unit
    def test_header_row_skipped(self, mapper, mock_repo, csv_with_header):
        """CSV with header row skips the header — only data rows counted."""
        count = mapper.load_company_profiles(csv_with_header, CATALOG_NAME, ASSET_CLASS)
        assert count == 2
        assert mock_repo.upsert.call_count == 2


# ---------------------------------------------------------------------------
# Task 2: Instrument ID Resolution Tests
# ---------------------------------------------------------------------------


class TestResolveInstrumentId:
    """Tests for InstrumentMapper.resolve_instrument_id()."""

    @pytest.mark.unit
    def test_resolve_existing_ticker(self, mapper, mock_repo):
        """Known ticker returns correct InstrumentId."""
        mock_instrument = CatalogInstrument(
            ticker="SPY",
            nautilus_id="SPY.ARCA",
            asset_class="ETF",
            catalog_name=CATALOG_NAME,
        )
        mock_repo.get_by_ticker.return_value = mock_instrument

        result = mapper.resolve_instrument_id("SPY", CATALOG_NAME)

        assert result == InstrumentId.from_str("SPY.ARCA")
        mock_repo.get_by_ticker.assert_called_once_with(CATALOG_NAME, "SPY")

    @pytest.mark.unit
    def test_resolve_unknown_ticker_raises(self, mapper, mock_repo):
        """Unknown ticker raises InstrumentMappingError."""
        mock_repo.get_by_ticker.return_value = None

        with pytest.raises(InstrumentMappingError, match="UNKNOWN"):
            mapper.resolve_instrument_id("UNKNOWN", CATALOG_NAME)

    @pytest.mark.unit
    def test_resolve_error_message_includes_ticker(self, mapper, mock_repo):
        """Error message includes the unmappable ticker."""
        mock_repo.get_by_ticker.return_value = None

        with pytest.raises(InstrumentMappingError, match="ZZZZ"):
            mapper.resolve_instrument_id("ZZZZ", CATALOG_NAME)


# ---------------------------------------------------------------------------
# Story 3.1: Venue qualification (nautilus_id from resolved venue + ADR-3 sync)
# ---------------------------------------------------------------------------


class TestQualification:
    """Tests for qualified_instrument_id() and sync_qualification()."""

    @pytest.mark.unit
    def test_qualified_instrument_id_from_venue(self, mapper):
        """A resolved venue yields a Nautilus-qualified InstrumentId."""
        assert mapper.qualified_instrument_id("SPY", "ARCA") == InstrumentId.from_str("SPY.ARCA")

    @pytest.mark.unit
    def test_qualified_instrument_id_none_venue_returns_none(self, mapper):
        """An unresolved (None) venue fabricates no identity (AC3)."""
        assert mapper.qualified_instrument_id("SPY", None) is None

    @pytest.mark.unit
    def test_qualified_instrument_id_blank_venue_returns_none(self, mapper):
        """A blank venue is treated as unresolved — no identity fabricated."""
        assert mapper.qualified_instrument_id("SPY", "") is None

    @pytest.mark.unit
    def test_sync_qualification_writes_nautilus_id_and_exchange(self, mapper, mock_repo):
        """Resolved venue overwrites the row's nautilus_id/exchange and upserts (ADR-3)."""
        row = CatalogInstrument(
            ticker="SPY",
            nautilus_id="SPY.NYSE",  # provisional CSV exchange
            exchange="NYSE",
            asset_class="ETF",
            catalog_name=CATALOG_NAME,
        )
        mock_repo.get_by_ticker.return_value = row

        result = mapper.sync_qualification("SPY", CATALOG_NAME, "ARCA")

        assert result == InstrumentId.from_str("SPY.ARCA")
        assert row.nautilus_id == "SPY.ARCA"
        assert row.exchange == "ARCA"
        mock_repo.upsert.assert_called_once_with(row)

    @pytest.mark.unit
    def test_sync_qualification_unresolved_leaves_nautilus_id_none(self, mapper, mock_repo):
        """A None venue leaves nautilus_id None (unqualified, not fabricated — AC3)."""
        row = CatalogInstrument(
            ticker="ZZZ",
            nautilus_id="ZZZ.NYSE",
            exchange="NYSE",
            asset_class="ETF",
            catalog_name=CATALOG_NAME,
        )
        mock_repo.get_by_ticker.return_value = row

        result = mapper.sync_qualification("ZZZ", CATALOG_NAME, None)

        assert result is None
        assert row.nautilus_id is None
        assert row.exchange is None
        mock_repo.upsert.assert_called_once_with(row)

    @pytest.mark.unit
    def test_sync_qualification_missing_row_returns_none_no_upsert(self, mapper, mock_repo):
        """No catalog_instruments row → defensive no-op (returns None, no upsert)."""
        mock_repo.get_by_ticker.return_value = None

        result = mapper.sync_qualification("SPY", CATALOG_NAME, "ARCA")

        assert result is None
        mock_repo.upsert.assert_not_called()


# ---------------------------------------------------------------------------
# Task 2: is_loaded Tests
# ---------------------------------------------------------------------------


class TestIsLoaded:
    """Tests for InstrumentMapper.is_loaded()."""

    @pytest.mark.unit
    def test_is_loaded_returns_true_when_instruments_exist(self, mapper, mock_repo):
        """Returns True when catalog has instruments."""
        mock_repo.list_by_catalog.return_value = [
            CatalogInstrument(
                ticker="SPY",
                nautilus_id="SPY.ARCA",
                asset_class="ETF",
                catalog_name=CATALOG_NAME,
            )
        ]

        assert mapper.is_loaded(CATALOG_NAME) is True

    @pytest.mark.unit
    def test_is_loaded_returns_false_when_empty(self, mapper, mock_repo):
        """Returns False when catalog has no instruments."""
        mock_repo.list_by_catalog.return_value = []

        assert mapper.is_loaded(CATALOG_NAME) is False
