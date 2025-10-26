# System Design: PostgreSQL Metadata Storage

**Feature**: PostgreSQL Metadata Storage for Backtest Execution
**Version**: 1.0
**Status**: Design Complete
**Date**: 2025-01-25

## Executive Summary

This document provides the complete system design for implementing PostgreSQL-based persistence of backtesting metadata and performance metrics. The design enables automatic, transparent persistence of all backtest executions with fast retrieval, comparison, and reproducibility capabilities.

**Key Design Decisions**:
- **Architecture**: Layered architecture with Repository pattern for data access
- **Database**: PostgreSQL 16+ with async SQLAlchemy 2.0 + asyncpg
- **Performance**: Cursor pagination with 3-phase index deployment
- **Configuration Storage**: JSONB with Pydantic validation
- **Integration**: Minimal changes to existing backtest execution flow

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Component Design](#component-design)
3. [Data Flow](#data-flow)
4. [Integration Points](#integration-points)
5. [CLI Design](#cli-design)
6. [Error Handling](#error-handling)
7. [Testing Strategy](#testing-strategy)
8. [Deployment Strategy](#deployment-strategy)
9. [Performance Optimization](#performance-optimization)
10. [Security Considerations](#security-considerations)

---

## System Architecture

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                    User Interface Layer                       │
│  ┌─────────────┬──────────────┬────────────┬──────────────┐  │
│  │ run.py      │ history.py   │ compare.py │ reproduce.py │  │
│  │ (modified)  │ (new)        │ (new)      │ (new)        │  │
│  └─────────────┴──────────────┴────────────┴──────────────┘  │
└─────────────────────────┬────────────────────────────────────┘
                          │ CLI Commands
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                     Service Layer                             │
│  ┌──────────────────────────────┬────────────────────────┐   │
│  │ BacktestPersistenceService   │ BacktestQueryService   │   │
│  │ - save_backtest_results()    │ - list_backtests()     │   │
│  │ - extract_metrics()          │ - get_backtest()       │   │
│  │ - serialize_config()         │ - compare_backtests()  │   │
│  └──────────────────────────────┴────────────────────────┘   │
└─────────────────────────┬────────────────────────────────────┘
                          │ Business Logic
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                    Repository Layer                           │
│  ┌──────────────────────────────────────────────────────┐    │
│  │         BacktestRepository                           │    │
│  │  - create_backtest_run()                            │    │
│  │  - create_performance_metrics()                     │    │
│  │  - find_by_id()                                     │    │
│  │  - find_recent()                                    │    │
│  │  - find_by_filter()                                 │    │
│  └──────────────────────────────────────────────────────┘    │
└─────────────────────────┬────────────────────────────────────┘
                          │ Data Access
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                     Data Layer                                │
│  ┌──────────────┬──────────────────┬───────────────────┐     │
│  │ SQLAlchemy   │ Database Session │ Connection Pool   │     │
│  │ Models       │ Management       │ (asyncpg)         │     │
│  └──────────────┴──────────────────┴───────────────────┘     │
└─────────────────────────┬────────────────────────────────────┘
                          │ SQL Queries
                          ▼
┌──────────────────────────────────────────────────────────────┐
│                  PostgreSQL Database                          │
│  ┌──────────────────┬────────────────────────────────┐       │
│  │ backtest_runs    │ performance_metrics            │       │
│  └──────────────────┴────────────────────────────────┘       │
└──────────────────────────────────────────────────────────────┘
```

### Architecture Layers

#### 1. CLI Layer (User Interface)
- **Responsibility**: User interaction, input validation, output formatting
- **Components**: Click commands, Rich console formatting
- **Error Handling**: User-friendly error messages, progress indicators

#### 2. Service Layer (Business Logic)
- **Responsibility**: Business rules, workflow orchestration, data transformation
- **Components**: Persistence service, Query service
- **Error Handling**: Business exception handling, logging

#### 3. Repository Layer (Data Access)
- **Responsibility**: Database operations, query construction, transaction management
- **Components**: BacktestRepository
- **Error Handling**: Database exception translation

#### 4. Data Layer (ORM)
- **Responsibility**: Object-relational mapping, connection pooling
- **Components**: SQLAlchemy models, async session management
- **Error Handling**: Connection errors, constraint violations

### Design Patterns

#### Repository Pattern
**Purpose**: Encapsulate data access logic and provide clean interface for business layer

**Benefits**:
- Separation of concerns (business logic vs data access)
- Testability (easy to mock repositories)
- Flexibility (can swap data sources)

#### Dependency Injection
**Purpose**: Provide loose coupling between components

**Implementation**:
```python
# Service layer receives repository via dependency injection
class BacktestPersistenceService:
    def __init__(self, repository: BacktestRepository):
        self.repository = repository
```

#### Context Manager Pattern
**Purpose**: Ensure proper resource cleanup (database sessions)

**Implementation**:
```python
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

---

## Component Design

### 1. Database Models (`src/db/models/backtest.py`)

**Purpose**: SQLAlchemy ORM models for database tables

**Classes**:
- `BacktestRun`: Represents backtest execution metadata
- `PerformanceMetrics`: Represents performance results

**Key Features**:
- Async SQLAlchemy 2.0 ORM
- Type hints with `Mapped[]`
- Database constraints and indexes
- Relationship mapping (one-to-one)

**Implementation**:
```python
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4
from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column, relationship
from src.db.base import Base

class BacktestRun(Base):
    """Backtest execution record."""
    __tablename__ = 'backtest_runs'

    # Primary key
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # Business identifier
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        default=uuid4,
        unique=True,
        nullable=False
    )

    # Metadata
    strategy_name: Mapped[str]
    strategy_type: Mapped[str]
    instrument_symbol: Mapped[str]

    # Date range
    start_date: Mapped[datetime]
    end_date: Mapped[datetime]

    # Execution details
    initial_capital: Mapped[Decimal]
    data_source: Mapped[str]
    execution_status: Mapped[str]
    execution_duration_seconds: Mapped[Decimal]
    error_message: Mapped[str | None]

    # Configuration snapshot (JSONB)
    config_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # Reproduction tracking
    reproduced_from_run_id: Mapped[UUID | None]

    # Timestamps
    created_at: Mapped[datetime]

    # Relationships
    metrics: Mapped["PerformanceMetrics | None"] = relationship(
        back_populates="backtest_run",
        cascade="all, delete-orphan",
        uselist=False
    )

    __table_args__ = (
        CheckConstraint('end_date > start_date'),
        CheckConstraint('initial_capital > 0'),
        Index('idx_backtest_runs_created_id', 'created_at', 'id'),
    )

class PerformanceMetrics(Base):
    """Performance metrics for successful backtests."""
    __tablename__ = 'performance_metrics'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    backtest_run_id: Mapped[int] = mapped_column(
        ForeignKey("backtest_runs.id", ondelete="CASCADE"),
        unique=True
    )

    # Return metrics
    total_return: Mapped[Decimal]
    final_balance: Mapped[Decimal]
    cagr: Mapped[Decimal | None]

    # Risk metrics
    sharpe_ratio: Mapped[Decimal | None]
    sortino_ratio: Mapped[Decimal | None]
    max_drawdown: Mapped[Decimal | None]
    max_drawdown_date: Mapped[datetime | None]
    calmar_ratio: Mapped[Decimal | None]
    volatility: Mapped[Decimal | None]

    # Trading metrics
    total_trades: Mapped[int]
    winning_trades: Mapped[int]
    losing_trades: Mapped[int]
    win_rate: Mapped[Decimal | None]
    profit_factor: Mapped[Decimal | None]
    expectancy: Mapped[Decimal | None]
    avg_win: Mapped[Decimal | None]
    avg_loss: Mapped[Decimal | None]

    created_at: Mapped[datetime]

    # Relationships
    backtest_run: Mapped[BacktestRun] = relationship(back_populates="metrics")

    __table_args__ = (
        CheckConstraint('total_trades = winning_trades + losing_trades'),
        Index('idx_metrics_backtest_run_id', 'backtest_run_id'),
    )
```

**Design Decisions**:
- ✅ Use `BIGSERIAL` for primary keys (future-proof for millions of records)
- ✅ UUID for business identifier (distributed-safe, no sequence conflicts)
- ✅ JSONB for config storage (queryable, validated)
- ✅ One-to-one relationship (metrics optional for failed backtests)
- ✅ Cascade deletes (metrics deleted with parent run)

---

### 2. Repository Layer (`src/db/repositories/backtest_repository.py`)

**Purpose**: Data access abstraction with async database operations

**Responsibilities**:
- Create backtest records
- Query backtests with filters
- Cursor pagination
- Transaction management

**Interface Design**:
```python
from typing import Optional, List, Tuple
from datetime import datetime
from uuid import UUID
from decimal import Decimal
from sqlalchemy import select, and_, or_, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from src.db.models.backtest import BacktestRun, PerformanceMetrics

class BacktestRepository:
    """Repository for backtest persistence and retrieval."""

    def __init__(self, session: AsyncSession):
        """Initialize repository with database session."""
        self.session = session

    async def create_backtest_run(
        self,
        run_id: UUID,
        strategy_name: str,
        strategy_type: str,
        instrument_symbol: str,
        start_date: datetime,
        end_date: datetime,
        initial_capital: Decimal,
        data_source: str,
        execution_status: str,
        execution_duration_seconds: Decimal,
        config_snapshot: dict,
        error_message: Optional[str] = None,
        reproduced_from_run_id: Optional[UUID] = None,
    ) -> BacktestRun:
        """
        Create a new backtest run record.

        Args:
            run_id: Unique business identifier
            strategy_name: Human-readable strategy name
            ... (other parameters)

        Returns:
            Created BacktestRun instance

        Raises:
            IntegrityError: If run_id already exists
            ValidationError: If data validation fails
        """
        backtest_run = BacktestRun(
            run_id=run_id,
            strategy_name=strategy_name,
            strategy_type=strategy_type,
            instrument_symbol=instrument_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            data_source=data_source,
            execution_status=execution_status,
            execution_duration_seconds=execution_duration_seconds,
            error_message=error_message,
            config_snapshot=config_snapshot,
            reproduced_from_run_id=reproduced_from_run_id,
        )

        self.session.add(backtest_run)
        await self.session.flush()  # Get ID before commit
        await self.session.refresh(backtest_run)

        return backtest_run

    async def create_performance_metrics(
        self,
        backtest_run_id: int,
        total_return: Decimal,
        final_balance: Decimal,
        cagr: Optional[Decimal],
        sharpe_ratio: Optional[Decimal],
        sortino_ratio: Optional[Decimal],
        max_drawdown: Optional[Decimal],
        max_drawdown_date: Optional[datetime],
        calmar_ratio: Optional[Decimal],
        volatility: Optional[Decimal],
        total_trades: int,
        winning_trades: int,
        losing_trades: int,
        win_rate: Optional[Decimal],
        profit_factor: Optional[Decimal],
        expectancy: Optional[Decimal],
        avg_win: Optional[Decimal],
        avg_loss: Optional[Decimal],
    ) -> PerformanceMetrics:
        """
        Create performance metrics for a backtest run.

        Args:
            backtest_run_id: Foreign key to backtest_runs.id
            total_return: Total return percentage
            ... (other metrics)

        Returns:
            Created PerformanceMetrics instance

        Raises:
            IntegrityError: If backtest_run_id already has metrics
        """
        metrics = PerformanceMetrics(
            backtest_run_id=backtest_run_id,
            total_return=total_return,
            final_balance=final_balance,
            cagr=cagr,
            sharpe_ratio=sharpe_ratio,
            sortino_ratio=sortino_ratio,
            max_drawdown=max_drawdown,
            max_drawdown_date=max_drawdown_date,
            calmar_ratio=calmar_ratio,
            volatility=volatility,
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=win_rate,
            profit_factor=profit_factor,
            expectancy=expectancy,
            avg_win=avg_win,
            avg_loss=avg_loss,
        )

        self.session.add(metrics)
        await self.session.flush()
        await self.session.refresh(metrics)

        return metrics

    async def find_by_run_id(self, run_id: UUID) -> Optional[BacktestRun]:
        """
        Find backtest by business identifier.

        Args:
            run_id: Unique business identifier

        Returns:
            BacktestRun with metrics loaded, or None if not found
        """
        stmt = (
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.run_id == run_id)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_recent(
        self,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        Find recent backtests with cursor pagination.

        Args:
            limit: Maximum number of records to return
            cursor: Pagination cursor (created_at, id) from last record

        Returns:
            List of BacktestRun instances with metrics loaded
        """
        stmt = select(BacktestRun).options(selectinload(BacktestRun.metrics))

        if cursor:
            created_at, id = cursor
            stmt = stmt.where(
                tuple_(BacktestRun.created_at, BacktestRun.id) < (created_at, id)
            )

        stmt = (
            stmt
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_strategy(
        self,
        strategy_name: str,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        Find backtests filtered by strategy name.

        Args:
            strategy_name: Strategy to filter by
            limit: Maximum records
            cursor: Pagination cursor

        Returns:
            List of matching BacktestRun instances
        """
        stmt = (
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.strategy_name == strategy_name)
        )

        if cursor:
            created_at, id = cursor
            stmt = stmt.where(
                and_(
                    BacktestRun.strategy_name == strategy_name,
                    tuple_(BacktestRun.created_at, BacktestRun.id) < (created_at, id)
                )
            )

        stmt = (
            stmt
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_by_run_ids(self, run_ids: List[UUID]) -> List[BacktestRun]:
        """
        Find multiple backtests by IDs (for comparison).

        Args:
            run_ids: List of business identifiers

        Returns:
            List of BacktestRun instances (may be fewer than requested)
        """
        stmt = (
            select(BacktestRun)
            .options(selectinload(BacktestRun.metrics))
            .where(BacktestRun.run_id.in_(run_ids))
            .order_by(BacktestRun.created_at.desc())
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_top_performers_by_sharpe(
        self,
        limit: int = 20,
    ) -> List[BacktestRun]:
        """
        Find top performing backtests by Sharpe ratio.

        Args:
            limit: Maximum records

        Returns:
            List of BacktestRun instances ordered by Sharpe ratio DESC
        """
        stmt = (
            select(BacktestRun)
            .join(PerformanceMetrics)
            .options(selectinload(BacktestRun.metrics))
            .where(PerformanceMetrics.sharpe_ratio.isnot(None))
            .order_by(PerformanceMetrics.sharpe_ratio.desc())
            .limit(limit)
        )

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_strategy(self, strategy_name: str) -> int:
        """
        Count backtests for a specific strategy.

        Args:
            strategy_name: Strategy to count

        Returns:
            Total count of matching backtests
        """
        from sqlalchemy import func

        stmt = (
            select(func.count(BacktestRun.id))
            .where(BacktestRun.strategy_name == strategy_name)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one()
```

**Design Decisions**:
- ✅ Async/await for all database operations
- ✅ Type hints for all parameters and return values
- ✅ Cursor pagination (not offset/limit) for performance
- ✅ Eager loading of relationships with `selectinload()`
- ✅ Transaction management delegated to session context manager

---

### 3. Service Layer

#### 3.1 Backtest Persistence Service (`src/services/backtest_persistence.py`)

**Purpose**: Orchestrate saving backtest results to database

**Responsibilities**:
- Extract metrics from EnhancedBacktestResult
- Validate metrics (NaN/Infinity checks)
- Serialize configuration to JSONB
- Coordinate repository calls
- Handle failures gracefully

**Implementation**:
```python
from typing import Optional
from uuid import UUID, uuid4
from decimal import Decimal
import math
import structlog
from src.models.backtest_result import EnhancedBacktestResult
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.models.backtest import BacktestRun, PerformanceMetrics

logger = structlog.get_logger(__name__)

class BacktestPersistenceService:
    """Service for persisting backtest results."""

    def __init__(self, repository: BacktestRepository):
        """Initialize with repository dependency."""
        self.repository = repository

    async def save_backtest_results(
        self,
        result: EnhancedBacktestResult,
        execution_duration_seconds: Decimal,
    ) -> UUID:
        """
        Save backtest results to database.

        Args:
            result: Enhanced backtest result from execution
            execution_duration_seconds: Time taken to run backtest

        Returns:
            UUID of created backtest run

        Raises:
            ValidationError: If metrics contain invalid values
            DatabaseError: If database operation fails
        """
        # Generate run ID
        run_id = uuid4()

        logger.info(
            "Saving backtest results",
            run_id=str(run_id),
            strategy=result.metadata.strategy_name,
            symbol=result.metadata.symbol
        )

        # Serialize configuration snapshot
        config_snapshot = self._serialize_config_snapshot(result)

        # Create backtest run record
        backtest_run = await self.repository.create_backtest_run(
            run_id=run_id,
            strategy_name=result.metadata.strategy_name,
            strategy_type=result.metadata.strategy_type,
            instrument_symbol=result.metadata.symbol,
            start_date=result.metadata.start_date,
            end_date=result.metadata.end_date,
            initial_capital=result.basic_result.initial_balance,
            data_source=result.metadata.data_source,
            execution_status="success",
            execution_duration_seconds=execution_duration_seconds,
            config_snapshot=config_snapshot,
            error_message=None,
        )

        # Extract and validate metrics
        validated_metrics = self._extract_and_validate_metrics(result)

        # Create performance metrics record
        await self.repository.create_performance_metrics(
            backtest_run_id=backtest_run.id,
            **validated_metrics
        )

        logger.info(
            "Backtest results saved successfully",
            run_id=str(run_id),
            backtest_run_id=backtest_run.id
        )

        return run_id

    async def save_failed_backtest(
        self,
        strategy_name: str,
        strategy_type: str,
        instrument_symbol: str,
        start_date,
        end_date,
        initial_capital: Decimal,
        data_source: str,
        config_snapshot: dict,
        error_message: str,
        execution_duration_seconds: Decimal,
    ) -> UUID:
        """
        Save failed backtest execution.

        Args:
            strategy_name: Name of strategy
            ... (other metadata)
            error_message: Error description
            execution_duration_seconds: Duration before failure

        Returns:
            UUID of created backtest run
        """
        run_id = uuid4()

        logger.warning(
            "Saving failed backtest",
            run_id=str(run_id),
            strategy=strategy_name,
            error=error_message
        )

        await self.repository.create_backtest_run(
            run_id=run_id,
            strategy_name=strategy_name,
            strategy_type=strategy_type,
            instrument_symbol=instrument_symbol,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            data_source=data_source,
            execution_status="failed",
            execution_duration_seconds=execution_duration_seconds,
            config_snapshot=config_snapshot,
            error_message=error_message,
        )

        logger.info("Failed backtest saved", run_id=str(run_id))

        return run_id

    def _serialize_config_snapshot(
        self,
        result: EnhancedBacktestResult
    ) -> dict:
        """
        Serialize strategy configuration to JSONB format.

        Args:
            result: Backtest result containing metadata

        Returns:
            Dictionary with config snapshot structure
        """
        return {
            "strategy_path": result.metadata.strategy_path,
            "config_path": result.metadata.config_path,
            "version": "1.0",
            "config": result.metadata.parameters
        }

    def _extract_and_validate_metrics(
        self,
        result: EnhancedBacktestResult
    ) -> dict:
        """
        Extract metrics from result and validate values.

        Args:
            result: Enhanced backtest result

        Returns:
            Dictionary of validated metrics

        Raises:
            ValidationError: If any metric is NaN or Infinity
        """
        perf = result.performance_metrics
        basic = result.basic_result

        return {
            "total_return": self._validate_metric(
                basic.total_return, "total_return"
            ),
            "final_balance": basic.final_balance,
            "cagr": self._validate_optional_metric(perf.cagr, "cagr"),
            "sharpe_ratio": self._validate_optional_metric(
                perf.sharpe_ratio, "sharpe_ratio"
            ),
            "sortino_ratio": self._validate_optional_metric(
                perf.sortino_ratio, "sortino_ratio"
            ),
            "max_drawdown": self._validate_optional_metric(
                perf.max_drawdown, "max_drawdown"
            ),
            "max_drawdown_date": perf.max_drawdown_date,
            "calmar_ratio": self._validate_optional_metric(
                perf.calmar_ratio, "calmar_ratio"
            ),
            "volatility": self._validate_optional_metric(
                perf.volatility, "volatility"
            ),
            "total_trades": basic.total_trades,
            "winning_trades": basic.winning_trades,
            "losing_trades": basic.losing_trades,
            "win_rate": self._calculate_win_rate(
                basic.winning_trades, basic.total_trades
            ),
            "profit_factor": self._validate_optional_metric(
                perf.profit_factor, "profit_factor"
            ),
            "expectancy": self._validate_optional_metric(
                perf.expectancy, "expectancy"
            ),
            "avg_win": self._calculate_avg_win(basic),
            "avg_loss": self._calculate_avg_loss(basic),
        }

    def _validate_metric(self, value: float, field_name: str) -> Decimal:
        """Validate metric is not NaN or Infinity."""
        if math.isnan(value):
            raise ValueError(f"{field_name} cannot be NaN")
        if math.isinf(value):
            raise ValueError(f"{field_name} cannot be Infinity")
        return Decimal(str(value))

    def _validate_optional_metric(
        self, value: Optional[float], field_name: str
    ) -> Optional[Decimal]:
        """Validate optional metric."""
        if value is None:
            return None
        return self._validate_metric(value, field_name)

    def _calculate_win_rate(
        self, winning_trades: int, total_trades: int
    ) -> Optional[Decimal]:
        """Calculate win rate percentage."""
        if total_trades == 0:
            return None
        return Decimal(str(winning_trades / total_trades))

    def _calculate_avg_win(self, basic_result) -> Optional[Decimal]:
        """Calculate average winning trade amount."""
        if basic_result.winning_trades == 0:
            return None
        # Extract from basic_result (implementation depends on data structure)
        return None  # Placeholder

    def _calculate_avg_loss(self, basic_result) -> Optional[Decimal]:
        """Calculate average losing trade amount."""
        if basic_result.losing_trades == 0:
            return None
        # Extract from basic_result (implementation depends on data structure)
        return None  # Placeholder
```

#### 3.2 Backtest Query Service (`src/services/backtest_query.py`)

**Purpose**: Business logic for querying and comparing backtests

**Implementation**:
```python
from typing import List, Optional, Tuple
from uuid import UUID
from datetime import datetime
from decimal import Decimal
import structlog
from src.db.repositories.backtest_repository import BacktestRepository
from src.db.models.backtest import BacktestRun

logger = structlog.get_logger(__name__)

class BacktestQueryService:
    """Service for querying backtest results."""

    def __init__(self, repository: BacktestRepository):
        """Initialize with repository dependency."""
        self.repository = repository

    async def get_backtest_by_id(self, run_id: UUID) -> Optional[BacktestRun]:
        """
        Retrieve complete backtest details by ID.

        Args:
            run_id: Business identifier

        Returns:
            BacktestRun with metrics, or None if not found
        """
        logger.debug("Fetching backtest", run_id=str(run_id))
        return await self.repository.find_by_run_id(run_id)

    async def list_recent_backtests(
        self,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        List recent backtests with pagination.

        Args:
            limit: Maximum records (default 20, max 1000)
            cursor: Pagination cursor from last result

        Returns:
            List of BacktestRun instances
        """
        # Enforce max limit
        limit = min(limit, 1000)

        logger.debug("Listing recent backtests", limit=limit)
        return await self.repository.find_recent(limit=limit, cursor=cursor)

    async def list_by_strategy(
        self,
        strategy_name: str,
        limit: int = 20,
        cursor: Optional[Tuple[datetime, int]] = None,
    ) -> List[BacktestRun]:
        """
        List backtests filtered by strategy.

        Args:
            strategy_name: Strategy to filter by
            limit: Maximum records
            cursor: Pagination cursor

        Returns:
            List of matching backtests
        """
        limit = min(limit, 1000)

        logger.debug(
            "Listing backtests by strategy",
            strategy=strategy_name,
            limit=limit
        )
        return await self.repository.find_by_strategy(
            strategy_name=strategy_name,
            limit=limit,
            cursor=cursor
        )

    async def compare_backtests(
        self, run_ids: List[UUID]
    ) -> List[BacktestRun]:
        """
        Retrieve multiple backtests for comparison.

        Args:
            run_ids: List of business identifiers (2-10)

        Returns:
            List of BacktestRun instances

        Raises:
            ValueError: If fewer than 2 or more than 10 IDs provided
        """
        if len(run_ids) < 2:
            raise ValueError("Must compare at least 2 backtests")
        if len(run_ids) > 10:
            raise ValueError("Cannot compare more than 10 backtests")

        logger.debug("Comparing backtests", count=len(run_ids))
        return await self.repository.find_by_run_ids(run_ids)

    async def find_top_performers(
        self, metric: str = "sharpe_ratio", limit: int = 20
    ) -> List[BacktestRun]:
        """
        Find top performing backtests by metric.

        Args:
            metric: Metric to sort by (sharpe_ratio, total_return, etc.)
            limit: Maximum records

        Returns:
            List of top performers
        """
        logger.debug("Finding top performers", metric=metric, limit=limit)

        if metric == "sharpe_ratio":
            return await self.repository.find_top_performers_by_sharpe(limit)
        else:
            raise ValueError(f"Unsupported metric: {metric}")
```

---

### 4. CLI Commands

#### 4.1 List Backtest History (`src/cli/commands/history.py`)

**Purpose**: Display recent backtests with filtering

**Command Signature**:
```bash
ntrader history [OPTIONS]

Options:
  --limit INTEGER         Number of results (default: 20, max: 1000)
  --strategy TEXT         Filter by strategy name
  --instrument TEXT       Filter by instrument symbol
  --status [success|failed]  Filter by execution status
  --sort [date|return|sharpe]  Sort order (default: date)
```

**Implementation**:
```python
import asyncio
import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from src.db.session import get_session
from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_query import BacktestQueryService

console = Console()

@click.command(name="history")
@click.option("--limit", default=20, type=int, help="Number of results")
@click.option("--strategy", default=None, type=str, help="Filter by strategy")
@click.option("--instrument", default=None, type=str, help="Filter by symbol")
def list_backtest_history(limit: int, strategy: str | None, instrument: str | None):
    """List recent backtest executions."""

    async def list_async():
        try:
            async with get_session() as session:
                repository = BacktestRepository(session)
                service = BacktestQueryService(repository)

                # Show progress spinner
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    transient=True,
                ) as progress:
                    task = progress.add_task("Loading backtest history...", total=None)

                    if strategy:
                        backtests = await service.list_by_strategy(
                            strategy_name=strategy,
                            limit=limit
                        )
                    else:
                        backtests = await service.list_recent_backtests(limit=limit)

                    progress.update(task, completed=True)

            if not backtests:
                console.print("📭 No backtests found", style="yellow")
                return

            # Format results in table
            table = Table(
                title=f"📋 Backtest History ({len(backtests)} results)",
                show_header=True,
                header_style="bold cyan",
                show_lines=True
            )

            table.add_column("Run ID", style="cyan", max_width=12)
            table.add_column("Date", style="white")
            table.add_column("Strategy", style="magenta")
            table.add_column("Symbol", style="blue")
            table.add_column("Return", style="green", justify="right")
            table.add_column("Sharpe", justify="right")
            table.add_column("Status", justify="center")

            for bt in backtests:
                metrics = bt.metrics

                status_emoji = "✅" if bt.execution_status == "success" else "❌"

                table.add_row(
                    str(bt.run_id)[:12] + "...",
                    bt.created_at.strftime("%Y-%m-%d %H:%M"),
                    bt.strategy_name,
                    bt.instrument_symbol,
                    f"{metrics.total_return:.2%}" if metrics else "N/A",
                    f"{metrics.sharpe_ratio:.2f}" if metrics and metrics.sharpe_ratio else "N/A",
                    status_emoji
                )

            console.print(table)
            console.print(f"\n✨ Showing {len(backtests)} of {limit} requested", style="dim")

        except Exception as e:
            console.print(f"❌ Error: {e}", style="red")
            raise

    asyncio.run(list_async())
```

#### 4.2 Compare Backtests (`src/cli/commands/compare.py`)

**Purpose**: Side-by-side comparison of multiple backtests

**Command Signature**:
```bash
ntrader compare <run-id-1> <run-id-2> [run-id-3] ...

Arguments:
  run-ids  2-10 backtest run identifiers
```

**Implementation**:
```python
import asyncio
import click
from uuid import UUID
from rich.console import Console
from rich.table import Table
from src.db.session import get_session
from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_query import BacktestQueryService

console = Console()

@click.command(name="compare")
@click.argument("run_ids", nargs=-1, required=True)
def compare_backtests(run_ids: tuple[str]):
    """Compare multiple backtests side-by-side."""

    async def compare_async():
        # Validate input
        if len(run_ids) < 2:
            console.print("❌ Must provide at least 2 run IDs", style="red")
            return
        if len(run_ids) > 10:
            console.print("❌ Cannot compare more than 10 backtests", style="red")
            return

        # Parse UUIDs
        try:
            parsed_ids = [UUID(run_id) for run_id in run_ids]
        except ValueError as e:
            console.print(f"❌ Invalid UUID format: {e}", style="red")
            return

        # Fetch backtests
        async with get_session() as session:
            repository = BacktestRepository(session)
            service = BacktestQueryService(repository)

            backtests = await service.compare_backtests(parsed_ids)

        if not backtests:
            console.print("❌ No backtests found", style="red")
            return

        # Create comparison table
        table = Table(
            title=f"📊 Backtest Comparison ({len(backtests)} runs)",
            show_header=True,
            header_style="bold cyan",
            show_lines=True
        )

        table.add_column("Metric", style="bold white")
        for bt in backtests:
            table.add_column(str(bt.run_id)[:8], justify="right")

        # Add rows for each metric
        metrics_to_compare = [
            ("Strategy", lambda bt: bt.strategy_name),
            ("Symbol", lambda bt: bt.instrument_symbol),
            ("Date", lambda bt: bt.created_at.strftime("%Y-%m-%d")),
            ("Total Return", lambda bt: f"{bt.metrics.total_return:.2%}" if bt.metrics else "N/A"),
            ("Sharpe Ratio", lambda bt: f"{bt.metrics.sharpe_ratio:.2f}" if bt.metrics and bt.metrics.sharpe_ratio else "N/A"),
            ("Max Drawdown", lambda bt: f"{bt.metrics.max_drawdown:.2%}" if bt.metrics and bt.metrics.max_drawdown else "N/A"),
            ("Win Rate", lambda bt: f"{bt.metrics.win_rate:.1%}" if bt.metrics and bt.metrics.win_rate else "N/A"),
            ("Total Trades", lambda bt: str(bt.metrics.total_trades) if bt.metrics else "N/A"),
        ]

        for metric_name, extractor in metrics_to_compare:
            row = [metric_name]
            row.extend([extractor(bt) for bt in backtests])
            table.add_row(*row)

        console.print(table)

        # Highlight best performer
        best_sharpe = max(
            (bt for bt in backtests if bt.metrics and bt.metrics.sharpe_ratio),
            key=lambda bt: bt.metrics.sharpe_ratio,
            default=None
        )

        if best_sharpe:
            console.print(
                f"\n🏆 Best Sharpe Ratio: {str(best_sharpe.run_id)[:12]} "
                f"({best_sharpe.metrics.sharpe_ratio:.2f})",
                style="bold green"
            )

    asyncio.run(compare_async())
```

---

## Data Flow

### 1. Backtest Execution with Auto-Save

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User Executes: ntrader run <strategy> <config>          │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. backtest_runner.py executes strategy                    │
│    - Generates UUID for run                                 │
│    - Records start time                                     │
│    - Loads configuration                                    │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Strategy Execution                                       │
│    - Process market data                                    │
│    - Generate signals                                       │
│    - Execute trades                                         │
└────────────────────────┬────────────────────────────────────┘
                         │
                   ┌─────┴─────┐
                   │           │
              Success      Failure
                   │           │
                   ▼           ▼
    ┌──────────────────┐  ┌────────────────────┐
    │ 4a. Calculate    │  │ 4b. Capture Error  │
    │     Metrics      │  │     Message        │
    └────────┬─────────┘  └─────────┬──────────┘
             │                      │
             ▼                      ▼
    ┌──────────────────┐  ┌────────────────────┐
    │ 5a. Persistence  │  │ 5b. Persistence    │
    │     Service      │  │     Service        │
    │  save_backtest_  │  │  save_failed_      │
    │    results()     │  │    backtest()      │
    └────────┬─────────┘  └─────────┬──────────┘
             │                      │
             ▼                      ▼
    ┌──────────────────┐  ┌────────────────────┐
    │ 6a. Create       │  │ 6b. Create         │
    │  BacktestRun +   │  │  BacktestRun only  │
    │  Metrics         │  │  (status=failed)   │
    └────────┬─────────┘  └─────────┬──────────┘
             │                      │
             └──────────┬───────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │ 7. Commit Transaction         │
        │    (session context manager)  │
        └───────────────┬───────────────┘
                        │
                        ▼
        ┌───────────────────────────────┐
        │ 8. Display Results to User    │
        │    - Run ID                   │
        │    - Performance summary      │
        │    - "Results saved to DB"    │
        └───────────────────────────────┘
```

### 2. Query Backtest History

```
┌─────────────────────────────────────────────────────────────┐
│ 1. User Executes: ntrader history --limit 20              │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. history.py CLI command                                  │
│    - Parse arguments                                        │
│    - Validate inputs                                        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. BacktestQueryService.list_recent_backtests()            │
│    - Apply limit constraints                                │
│    - Log query parameters                                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. BacktestRepository.find_recent()                        │
│    - Build SQLAlchemy query                                 │
│    - Apply cursor pagination                                │
│    - Eager load metrics                                     │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. PostgreSQL Query Execution                              │
│    - Use idx_backtest_runs_created_id                      │
│    - JOIN performance_metrics                               │
│    - Return 20 rows                                         │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ 6. Format Results with Rich                                │
│    - Create table                                           │
│    - Add rows with formatted metrics                        │
│    - Display to terminal                                    │
└─────────────────────────────────────────────────────────────┘
```

---

## Integration Points

### 1. Backtest Runner Integration (`src/core/backtest_runner.py`)

**Current Flow**:
```python
def run_backtest(config):
    # Load data
    # Execute strategy
    # Calculate metrics
    # Generate report
    # Return results
```

**Modified Flow**:
```python
async def run_backtest(config):
    """Run backtest with automatic persistence."""
    from src.db.session import get_session
    from src.db.repositories.backtest_repository import BacktestRepository
    from src.services.backtest_persistence import BacktestPersistenceService

    start_time = time.time()

    try:
        # Existing backtest execution
        result = execute_strategy(config)

        # Calculate metrics (existing)
        enhanced_result = calculate_metrics(result)

        # NEW: Persist to database
        execution_duration = Decimal(str(time.time() - start_time))

        async with get_session() as session:
            repository = BacktestRepository(session)
            persistence_service = BacktestPersistenceService(repository)

            run_id = await persistence_service.save_backtest_results(
                result=enhanced_result,
                execution_duration_seconds=execution_duration
            )

        # Display run ID to user
        print(f"✅ Backtest completed! Run ID: {run_id}")

        # Generate report (existing)
        generate_report(enhanced_result)

        return enhanced_result

    except Exception as e:
        # NEW: Save failed backtest
        execution_duration = Decimal(str(time.time() - start_time))

        async with get_session() as session:
            repository = BacktestRepository(session)
            persistence_service = BacktestPersistenceService(repository)

            run_id = await persistence_service.save_failed_backtest(
                strategy_name=config.strategy_name,
                strategy_type=config.strategy_type,
                instrument_symbol=config.symbol,
                start_date=config.start_date,
                end_date=config.end_date,
                initial_capital=config.initial_capital,
                data_source=config.data_source,
                config_snapshot=serialize_config(config),
                error_message=str(e),
                execution_duration_seconds=execution_duration
            )

        print(f"❌ Backtest failed. Run ID: {run_id}")
        raise
```

**Integration Checklist**:
- ✅ Add async/await support to backtest_runner
- ✅ Import persistence service
- ✅ Wrap save operations in try/except
- ✅ Don't block on database errors (log and continue)
- ✅ Display run_id to user
- ✅ Preserve existing report generation

### 2. Database Session Management (`src/db/session.py`)

**Expected Existing Code**:
```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.config import settings

# Async engine
engine = create_async_engine(
    settings.database_url,
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.debug
)

# Session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False
)
```

**Add Context Manager**:
```python
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Async context manager for database sessions.

    Yields:
        AsyncSession instance

    Handles:
        - Automatic commit on success
        - Automatic rollback on exception
        - Proper cleanup
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

---

## Error Handling

### Error Hierarchy

```python
class BacktestStorageError(Exception):
    """Base exception for backtest storage errors."""
    pass

class ValidationError(BacktestStorageError):
    """Raised when data validation fails."""
    pass

class DatabaseConnectionError(BacktestStorageError):
    """Raised when database connection fails."""
    pass

class DuplicateRecordError(BacktestStorageError):
    """Raised when attempting to create duplicate record."""
    pass

class RecordNotFoundError(BacktestStorageError):
    """Raised when queried record doesn't exist."""
    pass
```

### Error Handling Strategy

#### 1. Repository Layer
```python
from sqlalchemy.exc import IntegrityError, OperationalError

async def create_backtest_run(self, **kwargs):
    try:
        backtest_run = BacktestRun(**kwargs)
        self.session.add(backtest_run)
        await self.session.flush()
        return backtest_run

    except IntegrityError as e:
        if "unique constraint" in str(e.orig):
            raise DuplicateRecordError(f"Run ID already exists") from e
        raise

    except OperationalError as e:
        raise DatabaseConnectionError(f"Database connection failed: {e}") from e
```

#### 2. Service Layer
```python
async def save_backtest_results(self, result, duration):
    try:
        # Validate metrics
        validated_metrics = self._extract_and_validate_metrics(result)

    except (ValueError, TypeError) as e:
        raise ValidationError(f"Invalid metric values: {e}") from e

    try:
        # Save to database
        run_id = await self.repository.create_backtest_run(...)
        return run_id

    except DatabaseConnectionError:
        logger.error("Database unavailable, backtest results not saved")
        # Don't raise - allow backtest execution to complete
        return None
```

#### 3. CLI Layer
```python
try:
    backtests = await service.list_recent_backtests(limit)

except DatabaseConnectionError:
    console.print("❌ Database connection failed", style="red")
    console.print("💡 Check database is running and credentials are correct", style="dim")
    sys.exit(1)

except Exception as e:
    console.print(f"❌ Unexpected error: {e}", style="red")
    logger.exception("CLI command failed")
    sys.exit(1)
```

---

## Testing Strategy

### Test Pyramid

```
         ┌────────────────┐
         │  E2E Tests     │  5% - CLI integration
         │  (10 tests)    │
         ├────────────────┤
         │ Integration    │  20% - Database + Services
         │ Tests (40)     │
         ├────────────────┤
         │  Unit Tests    │  75% - Repository, Services, Models
         │  (150 tests)   │
         └────────────────┘
```

### Test Categories

#### 1. Unit Tests

**Database Models** (`tests/unit/models/test_backtest_models.py`):
```python
import pytest
from decimal import Decimal
from datetime import datetime
from uuid import uuid4
from src.db.models.backtest import BacktestRun, PerformanceMetrics

def test_backtest_run_creation():
    """Test BacktestRun model instantiation."""
    run = BacktestRun(
        run_id=uuid4(),
        strategy_name="SMA Crossover",
        strategy_type="trend_following",
        instrument_symbol="AAPL",
        start_date=datetime(2023, 1, 1),
        end_date=datetime(2023, 12, 31),
        initial_capital=Decimal("100000.00"),
        data_source="IBKR",
        execution_status="success",
        execution_duration_seconds=Decimal("45.237"),
        config_snapshot={"config": {}},
    )

    assert run.strategy_name == "SMA Crossover"
    assert run.execution_status == "success"

def test_performance_metrics_validation():
    """Test metric validation constraints."""
    with pytest.raises(ValueError):
        # Should fail: max_drawdown must be <= 0
        metrics = PerformanceMetrics(
            backtest_run_id=1,
            total_return=Decimal("0.25"),
            max_drawdown=Decimal("0.15"),  # Invalid: positive drawdown
            total_trades=10,
            winning_trades=6,
            losing_trades=4
        )
```

**Repository Layer** (`tests/unit/repositories/test_backtest_repository.py`):
```python
import pytest
from uuid import uuid4
from decimal import Decimal
from datetime import datetime
from src.db.repositories.backtest_repository import BacktestRepository

@pytest.fixture
async def repository(async_session):
    """Provide repository with test session."""
    return BacktestRepository(async_session)

@pytest.mark.asyncio
async def test_create_backtest_run(repository):
    """Test creating backtest run record."""
    run_id = uuid4()

    backtest_run = await repository.create_backtest_run(
        run_id=run_id,
        strategy_name="Test Strategy",
        strategy_type="test",
        instrument_symbol="TEST",
        start_date=datetime(2023, 1, 1),
        end_date=datetime(2023, 12, 31),
        initial_capital=Decimal("100000.00"),
        data_source="Mock",
        execution_status="success",
        execution_duration_seconds=Decimal("10.5"),
        config_snapshot={"config": {}}
    )

    assert backtest_run.run_id == run_id
    assert backtest_run.id is not None  # Auto-generated

@pytest.mark.asyncio
async def test_find_by_run_id(repository):
    """Test finding backtest by business ID."""
    # Create a run
    run_id = uuid4()
    await repository.create_backtest_run(run_id=run_id, ...)

    # Find it
    found = await repository.find_by_run_id(run_id)

    assert found is not None
    assert found.run_id == run_id

@pytest.mark.asyncio
async def test_cursor_pagination(repository):
    """Test cursor-based pagination."""
    # Create 50 backtest runs
    for i in range(50):
        await repository.create_backtest_run(...)

    # Get first page
    page1 = await repository.find_recent(limit=20)
    assert len(page1) == 20

    # Get second page using cursor
    cursor = (page1[-1].created_at, page1[-1].id)
    page2 = await repository.find_recent(limit=20, cursor=cursor)

    assert len(page2) == 20
    assert page2[0].created_at <= page1[-1].created_at
    # Ensure no duplicates
    page1_ids = {b.id for b in page1}
    page2_ids = {b.id for b in page2}
    assert page1_ids.isdisjoint(page2_ids)
```

#### 2. Integration Tests

**Database Integration** (`tests/integration/test_database_persistence.py`):
```python
import pytest
from decimal import Decimal
from datetime import datetime
from uuid import uuid4
from src.db.session import get_session
from src.db.repositories.backtest_repository import BacktestRepository
from src.services.backtest_persistence import BacktestPersistenceService

@pytest.mark.asyncio
async def test_full_persistence_flow():
    """Test complete persistence workflow."""
    async with get_session() as session:
        repository = BacktestRepository(session)
        service = BacktestPersistenceService(repository)

        # Create mock result
        mock_result = create_mock_backtest_result()

        # Save
        run_id = await service.save_backtest_results(
            result=mock_result,
            execution_duration_seconds=Decimal("30.5")
        )

        assert run_id is not None

    # Verify persistence (new session)
    async with get_session() as session:
        repository = BacktestRepository(session)
        found = await repository.find_by_run_id(run_id)

        assert found is not None
        assert found.metrics is not None
        assert found.metrics.total_return == mock_result.total_return
```

#### 3. End-to-End Tests

**CLI Integration** (`tests/e2e/test_cli_workflow.py`):
```python
import subprocess
import json
from uuid import UUID

def test_backtest_execution_with_persistence():
    """Test complete workflow: run backtest → save → query."""
    # Run backtest
    result = subprocess.run(
        ["ntrader", "run", "sma_crossover", "--config", "test_config.yaml"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Run ID:" in result.stdout

    # Extract run ID from output
    run_id = extract_run_id(result.stdout)

    # Query history
    result = subprocess.run(
        ["ntrader", "history", "--limit", "1"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert str(run_id) in result.stdout

def test_comparison_workflow():
    """Test comparing multiple backtests."""
    # Run two backtests
    run_id_1 = run_backtest("sma_crossover", "config1.yaml")
    run_id_2 = run_backtest("sma_crossover", "config2.yaml")

    # Compare
    result = subprocess.run(
        ["ntrader", "compare", str(run_id_1), str(run_id_2)],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0
    assert "Backtest Comparison" in result.stdout
    assert str(run_id_1) in result.stdout
    assert str(run_id_2) in result.stdout
```

---

## Deployment Strategy

### Phase 1: Database Setup

**1. Create Migration**
```bash
uv run alembic revision --autogenerate -m "create backtest storage tables"
```

**2. Review Generated Migration**
```python
# alembic/versions/xxx_create_backtest_storage_tables.py

def upgrade():
    # Create backtest_runs table
    op.create_table(
        'backtest_runs',
        # ... columns
    )

    # Create performance_metrics table
    op.create_table(
        'performance_metrics',
        # ... columns
    )

    # Create Phase 1 indexes
    op.create_index('idx_backtest_runs_created_id', ...)
    op.create_index('idx_backtest_runs_strategy_created_id', ...)
    op.create_index('idx_metrics_backtest_run_id', ...)

def downgrade():
    op.drop_table('performance_metrics')
    op.drop_table('backtest_runs')
```

**3. Test Migration**
```bash
# Test upgrade
uv run alembic upgrade head

# Test downgrade
uv run alembic downgrade -1

# Re-test upgrade
uv run alembic upgrade head
```

### Phase 2: Code Deployment

**Deployment Order**:
1. Deploy database models (`src/db/models/backtest.py`)
2. Deploy repository (`src/db/repositories/backtest_repository.py`)
3. Deploy services (`src/services/`)
4. Deploy CLI commands (`src/cli/commands/`)
5. Integrate with backtest_runner

**Rollback Plan**:
- Code can be rolled back independently
- Database migration can be downgraded: `alembic downgrade -1`
- No data loss (all operations are inserts only)

### Phase 3: Index Optimization (After 1 Week)

**Monitor query patterns** using PostgreSQL logs:
```sql
-- Enable query logging
ALTER DATABASE trading_ntrader SET log_statement = 'all';
ALTER DATABASE trading_ntrader SET log_duration = on;
ALTER DATABASE trading_ntrader SET log_min_duration_statement = 100;
```

**Deploy Phase 2 indexes** if needed:
```bash
uv run alembic revision -m "add phase 2 performance indexes"
```

```sql
-- Migration: Phase 2 indexes
CREATE INDEX idx_backtest_runs_symbol_created_id ...;
CREATE INDEX idx_backtest_runs_config_gin ...;
CREATE INDEX idx_metrics_sharpe_run ...;
```

---

## Performance Optimization

### Query Performance Targets

| Query Type | Target | Expected | Index Used |
|-----------|--------|----------|------------|
| Single by UUID | <100ms | 1-5ms | `uk_backtest_runs_run_id` |
| List 20 recent | <200ms | 10-20ms | `idx_backtest_runs_created_id` |
| Filter by strategy | <200ms | 15-30ms | `idx_backtest_runs_strategy_created_id` |
| Compare 10 runs | <2s | 5-15ms | `uk_backtest_runs_run_id` |
| Top 20 by Sharpe | <2s | 20-50ms | `idx_metrics_sharpe_run` (Phase 2) |
| JSONB search | <500ms | 50-200ms | `idx_backtest_runs_config_gin` (Phase 2) |

### Performance Monitoring

**Track query performance**:
```python
import time
from functools import wraps

def track_query_time(func):
    """Decorator to track query execution time."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        duration = time.perf_counter() - start

        logger.info(
            "Query completed",
            function=func.__name__,
            duration_ms=duration * 1000
        )

        return result
    return wrapper

class BacktestRepository:
    @track_query_time
    async def find_by_run_id(self, run_id):
        # ... implementation
```

**Database metrics**:
```sql
-- Check index usage
SELECT
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE tablename IN ('backtest_runs', 'performance_metrics')
ORDER BY idx_scan DESC;

-- Check slow queries
SELECT
    query,
    mean_exec_time,
    calls,
    total_exec_time
FROM pg_stat_statements
WHERE query LIKE '%backtest%'
ORDER BY mean_exec_time DESC
LIMIT 10;
```

---

## Security Considerations

### 1. Database Credentials

**Environment Variables**:
```bash
# .env (never commit)
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/trading_ntrader
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10
```

**Settings Management**:
```python
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    database_url: str
    database_pool_size: int = 20
    database_max_overflow: int = 10

    class Config:
        env_file = ".env"
```

### 2. SQL Injection Prevention

**Use Parameterized Queries** (SQLAlchemy handles this):
```python
# ✅ Safe - parameterized
stmt = select(BacktestRun).where(BacktestRun.run_id == run_id)

# ❌ Never do this
stmt = f"SELECT * FROM backtest_runs WHERE run_id = '{run_id}'"
```

### 3. Input Validation

**Pydantic Validation**:
```python
from pydantic import BaseModel, Field, field_validator

class BacktestRunCreate(BaseModel):
    strategy_name: str = Field(..., min_length=1, max_length=255)
    initial_capital: Decimal = Field(..., gt=0)

    @field_validator('strategy_name')
    @classmethod
    def validate_strategy_name(cls, v):
        if not v.replace('_', '').replace(' ', '').isalnum():
            raise ValueError("Invalid strategy name")
        return v
```

### 4. Error Message Sanitization

**Don't expose internal details**:
```python
try:
    await repository.create_backtest_run(...)
except IntegrityError as e:
    # ❌ Don't expose database details
    raise ValueError(f"Database error: {e.orig}")

    # ✅ Generic message
    raise DuplicateRecordError("Backtest with this ID already exists")
```

---

## Appendix

### A. File Size Compliance

All implementation files comply with the 500-line limit:

| File | Estimated Lines | Status |
|------|----------------|--------|
| `src/db/models/backtest.py` | ~150 | ✅ |
| `src/db/repositories/backtest_repository.py` | ~400 | ✅ |
| `src/services/backtest_persistence.py` | ~250 | ✅ |
| `src/services/backtest_query.py` | ~150 | ✅ |
| `src/cli/commands/history.py` | ~150 | ✅ |
| `src/cli/commands/compare.py` | ~180 | ✅ |
| `src/cli/commands/reproduce.py` | ~120 | ✅ |

### B. Technology Stack Summary

| Component | Technology | Version |
|-----------|-----------|---------|
| Database | PostgreSQL | 16+ |
| ORM | SQLAlchemy | 2.0+ |
| Async Driver | asyncpg | 0.30+ |
| Validation | Pydantic | 2.5+ |
| Migrations | Alembic | 1.16+ |
| CLI | Click | 8.1+ |
| Console | Rich | 13.7+ |
| Logging | structlog | 24.1+ |
| Testing | pytest | 7.4+ |
| Async Testing | pytest-asyncio | 0.21+ |

### C. Next Steps

1. **Generate Tasks** (`/speckit.tasks`)
   - Break design into actionable TDD tasks
   - Define acceptance criteria for each task
   - Order by dependency

2. **Implement Phase 1** (Database)
   - Create SQLAlchemy models
   - Write Alembic migration
   - Deploy to development database

3. **Implement Phase 2** (Repository)
   - Write repository with TDD
   - Implement cursor pagination
   - Add error handling

4. **Implement Phase 3** (Services)
   - Write persistence service
   - Write query service
   - Add metric validation

5. **Implement Phase 4** (CLI)
   - Create history command
   - Create compare command
   - Create reproduce command

6. **Integrate** (Backtest Runner)
   - Add async support
   - Integrate persistence service
   - Test end-to-end

---

**Status**: ✅ Design Complete
**Next Action**: Generate `tasks.md` using `/speckit.tasks` command
**Dependencies**: All research complete, data model finalized
**Estimated Implementation Time**: 3-4 weeks (with TDD)
