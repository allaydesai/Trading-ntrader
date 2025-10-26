"""SQLAlchemy declarative base for ORM models."""

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.

    Provides common functionality and timestamp fields for all models.
    """

    pass


class TimestampMixin:
    """
    Mixin to add created_at timestamp to models.

    Automatically sets created_at to current UTC time when record is created.
    """

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
    )
