"""Research MCP settings, read from ``NTRADER_MCP_*`` environment variables."""

from pathlib import Path, PurePosixPath

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config import CatalogSettings


class McpSettings(BaseSettings):
    """Where the server keeps jobs, where it may write in the vault, and its limits."""

    vault_path: Path | None = Field(
        default=None, description="Trading Research vault root; export is disabled when unset"
    )
    export_folders: list[str] = Field(
        default_factory=lambda: ["Lab/results"],
        description="Vault-relative folders export_results may write to",
    )
    jobs_dir: Path = Field(
        default_factory=lambda: Path.home() / ".ntrader" / "mcp" / "jobs",
        description="One directory per job: request, status, log, result",
    )
    default_catalog: str = Field(default="", description="Catalog used when a request names none")
    job_timeout_s: int = Field(default=3600, gt=0, description="Worker wall-clock limit")
    log_tail_lines: int = Field(default=100, gt=0, description="Log lines get_job returns")
    max_compare: int = Field(default=20, gt=1, description="Most runs compare_runs accepts")

    model_config = SettingsConfigDict(
        env_prefix="NTRADER_MCP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("export_folders")
    @classmethod
    def _folders_stay_inside_the_vault(cls, folders: list[str]) -> list[str]:
        for folder in folders:
            path = PurePosixPath(folder)
            if not folder or path.is_absolute() or ".." in path.parts:
                raise ValueError(f"export folder must be a vault-relative path: {folder!r}")
        return folders

    def resolved_default_catalog(self) -> str:
        """The MCP default, else NTrader's own default catalog (may be empty)."""
        return self.default_catalog or CatalogSettings().default_catalog_name
