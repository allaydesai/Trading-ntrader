"""Research MCP settings (env prefix NTRADER_MCP_)."""

from pathlib import Path

import pytest

from src.mcp_server.settings import McpSettings

pytestmark = pytest.mark.unit


def test_defaults(monkeypatch):
    for name in ("NTRADER_MCP_VAULT_PATH", "NTRADER_MCP_JOBS_DIR", "NTRADER_MCP_EXPORT_FOLDERS"):
        monkeypatch.delenv(name, raising=False)
    settings = McpSettings(_env_file=None)
    assert settings.vault_path is None
    assert settings.export_folders == ["Lab/results"]
    assert settings.jobs_dir == Path.home() / ".ntrader" / "mcp" / "jobs"
    assert settings.job_timeout_s == 3600
    assert settings.max_compare == 20


def test_reads_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("NTRADER_MCP_VAULT_PATH", str(tmp_path))
    monkeypatch.setenv("NTRADER_MCP_EXPORT_FOLDERS", '["Lab/results", "Experiments"]')
    monkeypatch.setenv("NTRADER_MCP_JOB_TIMEOUT_S", "60")
    settings = McpSettings(_env_file=None)
    assert settings.vault_path == tmp_path
    assert settings.export_folders == ["Lab/results", "Experiments"]
    assert settings.job_timeout_s == 60


@pytest.mark.parametrize("folder", ["/abs", "../escape", "Lab/../../x", ""])
def test_rejects_export_folders_that_could_escape_the_vault(monkeypatch, folder):
    monkeypatch.setenv("NTRADER_MCP_EXPORT_FOLDERS", f'["{folder}"]')
    with pytest.raises(ValueError):
        McpSettings(_env_file=None)


def test_default_catalog_falls_back_to_catalog_settings(monkeypatch):
    monkeypatch.delenv("NTRADER_MCP_DEFAULT_CATALOG", raising=False)
    monkeypatch.setenv("DEFAULT_CATALOG_NAME", "firstrate-etf")
    assert McpSettings(_env_file=None).resolved_default_catalog() == "firstrate-etf"
    monkeypatch.setenv("NTRADER_MCP_DEFAULT_CATALOG", "e2e-test")
    assert McpSettings(_env_file=None).resolved_default_catalog() == "e2e-test"
