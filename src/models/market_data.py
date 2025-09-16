"""Market data models for NTrader following data-model.md:48-69."""

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    Column,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class MarketDataBase(BaseModel):
    """Base market data model following data-model.md:48-69."""

    symbol: str = Field(..., min_length=1, max_length=20)
    timestamp: datetime
    open: Decimal = Field(..., gt=0, decimal_places=8)
    high: Decimal = Field(..., gt=0, decimal_places=8)
    low: Decimal = Field(..., gt=0, decimal_places=8)
    close: Decimal = Field(..., gt=0, decimal_places=8)
    volume: int = Field(..., ge=0)

    @field_validator("high")
    @classmethod
    def validate_high(cls, v, info):
        """Validate that high >= low."""
        if "low" in info.data and v < info.data["low"]:
            raise ValueError("High must be >= Low")
        return v

    @field_validator("low")
    @classmethod
    def validate_low(cls, v, info):
        """Validate that low <= high."""
        if "high" in info.data and v > info.data["high"]:
            raise ValueError("Low must be <= High")
        return v

    @field_validator("open", "high", "low", "close")
    @classmethod
    def validate_ohlc_range(cls, v, info):
        """Validate OHLC values are within high/low range."""
        if "high" in info.data and "low" in info.data:
            if v > info.data["high"] or v < info.data["low"]:
                raise ValueError(f"Price {v} must be between low and high")
        return v

    model_config = {
        "json_encoders": {
            Decimal: str,
            datetime: lambda v: v.isoformat(),
        }
    }


class MarketDataCreate(MarketDataBase):
    """Model for creating market data records."""

    pass


class MarketData(MarketDataBase):
    """Complete market data model with DB fields."""

    id: int
    created_at: datetime

    model_config = {"from_attributes": True}  # Enable ORM mode


class MarketDataTable(Base):
    """SQLAlchemy model for market_data table."""

    __tablename__ = "market_data"

    id = Column(BigInteger, primary_key=True, index=True)
    symbol = Column(String(20), nullable=False, index=True)
    timestamp = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    open = Column(Numeric(20, 8), nullable=False)
    high = Column(Numeric(20, 8), nullable=False)
    low = Column(Numeric(20, 8), nullable=False)
    close = Column(Numeric(20, 8), nullable=False)
    volume = Column(BigInteger, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("symbol", "timestamp", name="uq_symbol_timestamp"),
    )


# Create table object for direct SQL operations
market_data_table = MarketDataTable.__table__
