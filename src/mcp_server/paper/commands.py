"""The text of the commands that start a paper session for a frozen candidate (S7.1).

Pure. The server never runs them: Allay does, in a terminal. Every parameter the
frozen candidate sets is passed explicitly, so neither a strategy default nor a
setting in ``.env`` (``TRADE_SIZE``) can change it; a parameter frozen as None
cannot be passed and is left out with a warning. Every argument is shell-quoted.
"""

import shlex
from pathlib import Path
from typing import Any

from src.mcp_server.errors import ToolFailure

CLI = "uv run python -m src.cli.main live"
#: ``live create`` refuses longer names; ``trading_sessions.name`` holds no more.
MAX_NAME = 100


def param_value(value: Any) -> str | None:
    """A parameter as ``live create --param`` parses it, or None for an unset one."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, dict, set)):
        raise ToolFailure(
            "param_not_expressible",
            f"A {type(value).__name__} parameter cannot be passed as --param key=value.",
            fix="Start this candidate's session by hand; live create takes scalar params only.",
        )
    return str(value)


def live_bar_type(instrument_id: str, bar_spec: str) -> str:
    """The live bar type: the instrument, the run's bar spec, from the broker's own bars."""
    spec = bar_spec if bar_spec.endswith("-LAST") else f"{bar_spec}-LAST"
    return f"{instrument_id}-{spec}-EXTERNAL"


def check_name(name: str, *, taken: set[str]) -> str:
    """The session name, refused when ``live create`` would refuse it."""
    if not name.strip() or len(name) > MAX_NAME or name.startswith("-"):
        raise ToolFailure(
            "invalid_session_name",
            f"Session name {name!r} is blank, longer than {MAX_NAME} characters, or starts "
            "with '-' (live start would read it as an option).",
            fix="Pass another name, or none for the default.",
        )
    if name in taken:
        raise ToolFailure(
            "session_name_taken",
            f"A session named {name!r} already exists.",
            fix="Pass another name, or none for the default.",
        )
    return name


def default_name(slug: str, version: int, *, taken: set[str]) -> str:
    """``<slug>-v<version>-paper``, numbered when an earlier session has it."""
    base = f"{slug}-v{version}-paper"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    if len(name) > MAX_NAME:
        raise ToolFailure(
            "invalid_session_name",
            f"The default name {name!r} is longer than {MAX_NAME} characters.",
            fix="Pass a shorter name.",
        )
    return name


def _step(step: str, purpose: str, *words: str) -> dict[str, str]:
    return {"step": step, "purpose": purpose, "command": " ".join(words)}


def render_commands(
    *,
    repo_root: Path,
    name: str,
    strategy: str,
    bar_type: str,
    params: dict[str, Any],
    compare_to: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """The steps in order, and a warning for each parameter left out."""
    q = shlex.quote
    warnings, args = [], []
    for key, value in params.items():
        rendered = param_value(value)
        if rendered is None:
            warnings.append(
                f"Parameter {key} is unset (None) and is left out: live create resolves it "
                "from the strategy default or .env, which the backtest may not have used."
            )
            continue
        args += ["--param", q(f"{key}={rendered}")]
    create = [CLI, "create", "--name", q(name), "--strategy", q(strategy)]
    create += ["--bar-type", q(bar_type), *args, "--compare-to", q(compare_to)]
    commands = [
        _step("cd", "Run from the repo the candidate was frozen in", "cd", q(str(repo_root))),
        _step(
            "check",
            "Gateway reachable, paper account, bars subscribable",
            CLI,
            "check",
            "--bar-type",
            q(bar_type),
            "--observe-seconds",
            "0",
        ),
        _step("create", "Freeze the session spec, linked to the out-of-sample run", *create),
        _step("start", "Start trading (runs in the foreground)", CLI, "start", q(name)),
        _step(
            "status",
            "From another terminal: is it alive and receiving bars?",
            CLI,
            "status",
            q(name),
        ),
    ]
    return commands, warnings
