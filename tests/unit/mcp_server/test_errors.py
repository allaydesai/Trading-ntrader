"""Expected tool failures carry a code, a message and the fix."""

import pytest

from src.mcp_server.errors import ToolFailure

pytestmark = pytest.mark.unit


def test_to_dict():
    err = ToolFailure("unknown_catalog", "No catalog 'x'.", fix="Use one of: a, b")
    assert err.to_dict() == {
        "ok": False,
        "error": {
            "code": "unknown_catalog",
            "message": "No catalog 'x'.",
            "fix": "Use one of: a, b",
        },
    }
    assert str(err) == "No catalog 'x'."


def test_details_are_included():
    err = ToolFailure("invalid_params", "Bad.", fix="Fix.", details={"fields": ["a"]})
    assert err.to_dict()["error"]["details"] == {"fields": ["a"]}
