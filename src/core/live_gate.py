"""Pure paper/live safety gate — no I/O, no framework imports.

Layer 1 of the two-layer safety design: the decision to permit a broker
connection is made from typed configuration alone, before any socket is opened.
``evaluate_gate`` returns a :class:`GateDecision` — it never raises and never
exits, so callers decide what a refusal means (the node builder raises, the CLI
maps it to an exit code).

Layer 2 — verifying the account the gateway actually reports after connecting —
lives elsewhere and is deliberately not this module's concern.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import IBKRSettings


PAPER_PORTS: frozenset[int] = frozenset({7497, 4002})
PAPER_ACCOUNT_PREFIXES: tuple[str, ...] = ("DU", "DF")
ACCOUNT_MASK_VISIBLE_CHARS = 3


class GateMode(str, Enum):
    """The kind of connection a decision applies to."""

    PAPER = "paper"
    REAL_MONEY = "real_money"


class GateRefusalReason(str, Enum):
    """The specific condition that caused a refusal."""

    NON_PAPER_TRADING_MODE = "non_paper_trading_mode"
    NON_PAPER_PORT = "non_paper_port"
    NON_PAPER_ACCOUNT_PREFIX = "non_paper_account_prefix"
    REAL_MONEY_FLAG_WITHOUT_ENV = "real_money_flag_without_env"
    REAL_MONEY_ENV_WITHOUT_FLAG = "real_money_env_without_flag"
    REAL_MONEY_ACCOUNT_MISMATCH = "real_money_account_mismatch"


@dataclass(frozen=True)
class GateFlags:
    """Operator declarations that arrive from the command line.

    Attributes:
        real_money: True when the operator passed ``--real-money``.
    """

    real_money: bool = False


@dataclass(frozen=True)
class GateRefusal:
    """Why a connection was refused.

    Attributes:
        reason: The specific failing condition.
        message: Operator-facing sentence, already masked at construction.
    """

    reason: GateRefusalReason
    message: str


@dataclass(frozen=True)
class GateDecision:
    """The gate's verdict.

    The invariant ``permitted is (refusal is None)`` holds on every path — a
    decision is never half-refused.

    Attributes:
        permitted: True when the connection may proceed.
        mode: The kind of connection that was evaluated.
        refusal: The refusal detail, or None when permitted.
    """

    permitted: bool
    mode: GateMode
    refusal: GateRefusal | None = None


def mask_account(account: str) -> str:
    """Mask an account identifier down to its last few characters.

    Args:
        account: Raw account identifier, possibly empty.

    Returns:
        An empty string for an empty account, ``"***"`` for an account too
        short to reveal anything, otherwise ``"***"`` plus the last three
        characters.
    """
    if not account:
        return ""
    if len(account) <= ACCOUNT_MASK_VISIBLE_CHARS:
        return "***"
    return f"***{account[-ACCOUNT_MASK_VISIBLE_CHARS:]}"


def evaluate_gate(settings: "IBKRSettings", cli_flags: GateFlags) -> GateDecision:
    """Decide whether a broker connection is permitted, from configuration alone.

    Performs no I/O: no socket, no file, no database. Either declaration of a
    real-money crossing routes to the crossing rules; everything else must
    satisfy all three paper conditions (mode, port, account prefix).

    Args:
        settings: Loaded IBKR settings supplying the four gate-relevant fields.
        cli_flags: Declarations the operator made on the command line.

    Returns:
        A GateDecision permitting the connection, or carrying a GateRefusal that
        names the specific failing condition.
    """
    env_account = settings.ntrader_real_money_account.strip()
    account = settings.tws_account.strip()

    if cli_flags.real_money or env_account:
        return _evaluate_real_money_crossing(
            real_money_flag=cli_flags.real_money,
            env_account=env_account,
            account=account,
        )

    return _evaluate_paper(
        trading_mode=settings.ibkr_trading_mode,
        port=settings.ibkr_port,
        account=account,
    )


def _evaluate_real_money_crossing(
    *, real_money_flag: bool, env_account: str, account: str
) -> GateDecision:
    """Apply the two-declaration crossing rules.

    Only both declarations present and matching exactly permits real money; the
    absence of either is a refusal, never a question.
    """
    if not env_account:
        return _refuse(
            GateMode.REAL_MONEY,
            GateRefusalReason.REAL_MONEY_FLAG_WITHOUT_ENV,
            "Real-money trading was requested with --real-money, but "
            "NTRADER_REAL_MONEY_ACCOUNT is not set. Both declarations are required.",
        )

    if not real_money_flag:
        return _refuse(
            GateMode.REAL_MONEY,
            GateRefusalReason.REAL_MONEY_ENV_WITHOUT_FLAG,
            f"NTRADER_REAL_MONEY_ACCOUNT authorizes account {mask_account(env_account)}, "
            "but --real-money was not passed. Unset the variable to run paper trading.",
        )

    if not account or env_account != account:
        return _refuse(
            GateMode.REAL_MONEY,
            GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH,
            f"Authorized real-money account {mask_account(env_account) or '(unset)'} does not "
            f"match the configured TWS_ACCOUNT {mask_account(account) or '(unset)'}. The two "
            "declarations must name the same account exactly.",
        )

    return GateDecision(permitted=True, mode=GateMode.REAL_MONEY)


def _evaluate_paper(*, trading_mode: str, port: int, account: str) -> GateDecision:
    """Require all three paper conditions; an unrecognised port fails closed."""
    if trading_mode != "paper":
        return _refuse(
            GateMode.PAPER,
            GateRefusalReason.NON_PAPER_TRADING_MODE,
            f"IBKR_TRADING_MODE is '{trading_mode}', not 'paper'. Paper trading is the only "
            "permitted mode without an explicit real-money authorization.",
        )

    if port not in PAPER_PORTS:
        permitted_ports = ", ".join(str(known) for known in sorted(PAPER_PORTS))
        return _refuse(
            GateMode.PAPER,
            GateRefusalReason.NON_PAPER_PORT,
            f"IBKR_PORT {port} is not a known paper port ({permitted_ports}). Any other port "
            "is refused, including unrecognised ones.",
        )

    if account and not account.upper().startswith(PAPER_ACCOUNT_PREFIXES):
        paper_prefixes = "/".join(PAPER_ACCOUNT_PREFIXES)
        return _refuse(
            GateMode.PAPER,
            GateRefusalReason.NON_PAPER_ACCOUNT_PREFIX,
            f"TWS_ACCOUNT {mask_account(account)} does not start with a paper account prefix "
            f"({paper_prefixes}), so it may be a real-money account.",
        )

    return GateDecision(permitted=True, mode=GateMode.PAPER)


def _refuse(mode: GateMode, reason: GateRefusalReason, message: str) -> GateDecision:
    """Build a refusal decision, keeping the permitted/refusal invariant intact."""
    return GateDecision(
        permitted=False,
        mode=mode,
        refusal=GateRefusal(reason=reason, message=message),
    )
