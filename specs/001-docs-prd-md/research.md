# Research: Nautilus Trader Backtesting System with IBKR Integration

**Date**: 2025-01-13  
**Phase**: 0 - Technology Research and Architecture Decisions

## Core Framework Decision

**Decision**: Nautilus Trader as primary backtesting framework  
**Rationale**: 
- Production-grade event-driven backtesting engine written in Rust/Cython for performance
- Native Interactive Brokers integration via `nautilus_trader[ib]` package
- Maintains parity between backtesting and live trading (same strategy code)
- Supports multiple asset classes (equities, FX, futures, options, crypto)
- High-performance with nanosecond precision timing
- Handles realistic order execution, slippage, and commission modeling

**Alternatives considered**:
- Backtrader: Python-only, slower performance, no native IBKR integration
- Zipline: Discontinued, US equities only, no live trading parity
- Custom solution: Would require months of development for equivalent features

## Interactive Brokers Integration

**Decision**: Use Nautilus Trader's official IBKR adapter with dockerized Gateway  
**Rationale**:
- Official support for TWS/IB Gateway connections
- Handles rate limiting (IBKR limits: 50 requests/second)
- Supports paper and live trading modes
- Comprehensive market data types (real-time, delayed, frozen)
- Robust connection management with automatic reconnection
- Docker Gateway approach provides isolation and reliability

**Alternatives considered**:
- ib_insync: Would require custom integration with Nautilus Trader
- Direct TWS API: Complex low-level implementation
- Third-party data providers: Higher costs, potential data quality issues

## Data Storage Architecture

**Decision**: PostgreSQL with TimescaleDB extension  
**Rationale**:
- TimescaleDB optimized for time-series data (OHLCV bars, tick data)
- Native PostgreSQL compatibility with existing ecosystem
- Efficient data compression and partitioning
- Supports complex queries for performance analysis
- Aligns with constitution's PostgreSQL preference

**Alternatives considered**:
- InfluxDB: Less Python ecosystem support, different query language
- Redis: Not suitable for large historical datasets
- Parquet files: Limited query capabilities, harder concurrent access

## API Framework

**Decision**: FastAPI with async/await patterns  
**Rationale**:
- Constitutional requirement for FastAPI-first architecture
- Automatic OpenAPI documentation generation
- High performance with async support
- Native Pydantic integration for data validation
- Perfect for backtesting control APIs and result serving

**Alternatives considered**: N/A (constitutionally mandated)

## Testing Strategy

**Decision**: pytest with Test-Driven Development approach  
**Rationale**:
- Constitutional requirement for TDD (tests before implementation)
- pytest-asyncio for testing async FastAPI endpoints
- Comprehensive fixture system for trading scenarios
- Integration with coverage tools for 80% minimum coverage
- Supports parameterized testing for multiple strategies/instruments

**Alternatives considered**: N/A (constitutionally mandated)

## Performance Libraries

**Decision**: NumPy + Pandas for data processing, Polars for performance-critical operations  
**Rationale**:
- NumPy provides vectorized operations for technical indicators
- Pandas excellent for time-series data manipulation (OHLCV bars)
- Polars for high-performance operations on large datasets
- Native integration with Nautilus Trader's data structures
- Constitutional alignment with data processing stack

**Alternatives considered**:
- Pure Python: Too slow for large backtests
- Apache Arrow: Overkill for single-user system
- Dask: Unnecessary complexity for current scale

## Visualization and Reporting

**Decision**: HTML reports with embedded charts, CSV/JSON exports  
**Rationale**:
- HTML reports provide rich visualizations (equity curves, drawdown charts)
- CSV exports for further analysis in Excel/other tools
- JSON format for programmatic consumption
- Lightweight approach avoiding heavy GUI frameworks

**Alternatives considered**:
- Jupyter notebooks: Complex deployment, security concerns
- Desktop GUI: Increased complexity, deployment challenges
- Web dashboard: Scope creep beyond current requirements

## Development Environment

**Decision**: UV package manager with Docker for IBKR Gateway  
**Rationale**:
- Constitutional requirement for UV exclusive usage
- Docker isolation for IBKR Gateway provides reliability
- Simplified dependency management and reproducible builds
- Fast installation and environment setup

**Alternatives considered**: N/A (constitutionally mandated)

## Configuration Management

**Decision**: Pydantic Settings with environment variables  
**Rationale**:
- Constitutional standard for configuration
- Type-safe configuration with validation
- Environment variable support for deployment flexibility
- Integration with FastAPI dependency injection

**Alternatives considered**: N/A (constitutionally mandated)

## Logging and Observability

**Decision**: structlog with correlation IDs  
**Rationale**:
- Constitutional requirement for structured logging
- Correlation IDs for tracing backtest execution
- JSON output format for log aggregation
- Integration with Nautilus Trader's logging system

**Alternatives considered**: N/A (constitutionally mandated)

## Project Structure Validation

**Decision**: Standard Python backend structure per constitution  
**Rationale**:
- Follows constitutional project structure requirements
- Clear separation of concerns (api/, core/, models/, services/)
- Test files mirror source structure
- Supports future expansion to web interface if needed

**Structure**:
```
src/
├── api/          # FastAPI routers for backtest control
├── core/         # Trading strategies and business logic
├── models/       # Pydantic schemas for data validation
├── services/     # IBKR client, backtesting engine wrapper
├── db/           # Database models and migrations
└── utils/        # Shared utilities and helpers

tests/            # Mirror src structure with test_ prefix
scripts/          # CLI tools for data management
docs/             # Documentation and ADRs
```

## Risk Considerations

1. **IBKR Rate Limits**: Handled by Nautilus Trader's built-in rate limiting
2. **Data Quality**: IBKR provides high-quality institutional data
3. **System Performance**: Rust core provides necessary speed for large backtests
4. **Maintainability**: Well-documented, actively maintained dependencies
5. **Scalability**: Architecture supports future multi-user deployment

## Next Steps

All technical unknowns resolved. Ready to proceed to Phase 1 (Design & Contracts) with:
- Data model design based on Nautilus Trader entities
- API contract specification for backtest management
- Integration patterns for IBKR data flow
- Performance reporting structure design