"""Tool registration. Each module adds a group of thin tools that call a service.

Tools never raise for an expected failure: ``call`` returns ``{"ok": False,
"error": {code, message, fix}}`` so the client sees what to do next, and runs
the (sync, DB-touching) service off the event loop.
"""

from collections.abc import Callable
from functools import partial
from typing import Any

import anyio
from pydantic import ValidationError

from src.mcp_server.errors import ToolFailure
from src.mcp_server.request import BacktestSpec


async def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Run ``fn`` in a worker thread; wrap its result or its ``ToolFailure``."""
    try:
        result = await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))
    except ToolFailure as failure:
        return failure.to_dict()
    return {"ok": True, **result}


def build_spec(**fields: Any) -> BacktestSpec:
    """A ``BacktestSpec`` from tool arguments, or a ``ToolFailure`` naming the bad field."""
    try:
        return BacktestSpec(**fields)
    except ValidationError as exc:
        first = exc.errors()[0]
        field = ".".join(str(p) for p in first["loc"])
        raise ToolFailure(
            "invalid_request", f"{field}: {first['msg']}", fix="Correct the request fields."
        ) from None
