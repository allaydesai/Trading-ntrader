"""Expected tool failures: a stable code, a plain message and the fix."""

from typing import Any


class ToolFailure(Exception):
    """A failure the caller can act on, returned to the client as data, not a crash."""

    def __init__(
        self, code: str, message: str, *, fix: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        """The tool result shape for a failure."""
        error: dict[str, Any] = {"code": self.code, "message": self.message, "fix": self.fix}
        if self.details is not None:
            error["details"] = self.details
        return {"ok": False, "error": error}
