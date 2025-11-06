"""
Custom SQLAlchemy TypeDecorator for validated JSONB fields.

This module provides a TypeDecorator that validates JSONB data using Pydantic
models before storing in the database.
"""

from typing import Any, Optional, Type

from pydantic import BaseModel, ValidationError
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

from src.models.config_snapshot import StrategyConfigSnapshot


class ValidatedJSONB(TypeDecorator):
    """
    SQLAlchemy TypeDecorator for JSONB with Pydantic validation.

    This decorator validates JSONB data against a Pydantic model schema
    before storing it in the database, ensuring data integrity.

    Attributes:
        impl: The underlying SQL type (JSON/JSONB)
        cache_ok: Whether this type is safe to cache
        validation_model: Pydantic model class for validation

    Example:
        >>> from sqlalchemy.orm import Mapped, mapped_column
        >>> class MyModel(Base):
        ...     config: Mapped[dict] = mapped_column(ValidatedJSONB)
    """

    impl = JSON
    cache_ok = True

    def __init__(
        self,
        validation_model: Type[BaseModel] = StrategyConfigSnapshot,
        *args: Any,
        **kwargs: Any,
    ):
        """
        Initialize the ValidatedJSONB type.

        Args:
            validation_model: Pydantic model class for validation
            *args: Additional positional arguments for TypeDecorator
            **kwargs: Additional keyword arguments for TypeDecorator
        """
        super().__init__(*args, **kwargs)
        self.validation_model = validation_model

    def process_bind_param(self, value: Optional[dict], dialect: Any) -> Optional[dict]:
        """
        Validate data before storing in database.

        Args:
            value: Dictionary to validate and store
            dialect: SQL dialect (unused)

        Returns:
            Validated dictionary ready for storage

        Raises:
            ValidationError: If data doesn't match validation model schema
        """
        if value is None:
            return None

        try:
            # Validate using Pydantic model
            validated = self.validation_model(**value)
            return validated.model_dump()
        except ValidationError as e:
            raise ValueError(f"Invalid config_snapshot structure: {e}") from e

    def process_result_value(
        self, value: Optional[dict], dialect: Any
    ) -> Optional[dict]:
        """
        Process data retrieved from database.

        Args:
            value: Dictionary from database
            dialect: SQL dialect (unused)

        Returns:
            Dictionary (no transformation needed)
        """
        # Return as-is; validation only needed on write
        return value
