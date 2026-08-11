"""Pure paper/live safety gate — no I/O, no framework imports.

Both layers of the two-layer safety design *decide* here, because both decisions
are pure functions of typed configuration plus (for Layer 2) the account
identifiers the gateway named. Neither raises and neither exits, so callers
decide what a refusal means (the node builder raises, the CLI maps it to an exit
code).

- **Layer 1** — ``evaluate_gate``: permits a connection from configuration
  alone, before any socket is opened.
- **Layer 2** — ``evaluate_account_gate``: judges what the connected gateway
  actually reported. It begins by running Layer 1 and returning its refusal
  unchanged, so it is structurally Layer-1-AND-more and can never permit
  something Layer 1 refused.

What is deliberately *not* here is Layer 2's impure half — obtaining the
gateway's account list and shutting a node down on a refusal. That lives in
``src/core/live_account_gate.py``, which may import Nautilus; this module may
not.
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
    """The specific condition that caused a refusal.

    The first six are Layer 1's, decided from configuration alone. The rest are
    Layer 2's, decided once the gateway has spoken — the last two of those are
    raised by the enforcement seam (``src/core/live_account_gate.py``) rather
    than by a decision function, because they describe *when* verification was
    called rather than *what* was reported.
    """

    NON_PAPER_TRADING_MODE = "non_paper_trading_mode"
    NON_PAPER_PORT = "non_paper_port"
    NON_PAPER_ACCOUNT_PREFIX = "non_paper_account_prefix"
    REAL_MONEY_FLAG_WITHOUT_ENV = "real_money_flag_without_env"
    REAL_MONEY_ENV_WITHOUT_FLAG = "real_money_env_without_flag"
    REAL_MONEY_ACCOUNT_MISMATCH = "real_money_account_mismatch"
    ACCOUNT_NOT_REPORTED = "account_not_reported"
    REPORTED_ACCOUNT_NOT_PAPER = "reported_account_not_paper"
    REPORTED_ACCOUNT_NOT_AUTHORIZED = "reported_account_not_authorized"
    REPORTED_ACCOUNT_IS_PAPER = "reported_account_is_paper"
    NODE_NOT_CONNECTED = "node_not_connected"
    STRATEGY_STARTED_BEFORE_ACCOUNT_GATE = "strategy_started_before_account_gate"
    ACCOUNT_VERIFICATION_ERROR = "account_verification_error"


@dataclass(frozen=True)
class GateFlags:
    """Operator declarations that arrive from the command line.

    Attributes:
        real_money: True when the operator passed ``--real-money``. Compared by
            identity against ``True`` everywhere it is read, so a non-bool that
            merely happens to be truthy — the string ``"false"``, say, arriving
            from a config file or a Click option missing ``is_flag=True`` — can
            never be mistaken for consent.
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
        mode: The kind of connection that was permitted, or None on a refusal.
            A refused attempt has no established mode: the branch that rejected
            it says nothing about what the operator intended, so reporting one
            would misdescribe the attempt in whichever direction it failed.
            ``refusal.reason`` already identifies the branch unambiguously.
        refusal: The refusal detail, or None when permitted.
    """

    permitted: bool
    mode: GateMode | None
    refusal: GateRefusal | None = None


def mask_account(account: str) -> str:
    """Mask an account identifier down to its last few characters.

    Normalizes its own input rather than trusting the caller to have done it:
    the helper is public and is reused for identifiers the gateway reports,
    which do not pass through ``evaluate_gate``'s stripping and may carry
    trailing whitespace or newlines into a log line.

    Args:
        account: Raw account identifier, possibly empty or padded.

    Returns:
        An empty string for an empty account, ``"***"`` for an account short
        enough that revealing three characters would disclose most of it,
        otherwise ``"***"`` plus the last three characters.
    """
    normalized = account.strip()
    if not normalized:
        return ""
    # Reveal the tail only when it stays a minority of the value: at length 4,
    # showing three characters would disclose 75% of the identifier.
    if len(normalized) <= 2 * ACCOUNT_MASK_VISIBLE_CHARS:
        return "***"
    return f"***{normalized[-ACCOUNT_MASK_VISIBLE_CHARS:]}"


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
    # Identity, not truthiness: only a genuine bool True is consent.
    real_money_flag = cli_flags.real_money is True

    if real_money_flag or env_account:
        return _evaluate_real_money_crossing(
            real_money_flag=real_money_flag,
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
        return build_refusal(
            GateRefusalReason.REAL_MONEY_FLAG_WITHOUT_ENV,
            "Real-money trading was requested with --real-money, but "
            "NTRADER_REAL_MONEY_ACCOUNT is not set. Both declarations are required.",
        )

    if not real_money_flag:
        return build_refusal(
            GateRefusalReason.REAL_MONEY_ENV_WITHOUT_FLAG,
            f"NTRADER_REAL_MONEY_ACCOUNT authorizes account {mask_account(env_account)}, "
            "but --real-money was not passed. Unset the variable to run paper trading.",
        )

    # Split from the mismatch case below: an absent TWS_ACCOUNT is a different
    # operator error from two declarations that disagree, and rendering it as a
    # comparison would print a mask against nothing.
    if not account:
        return build_refusal(
            GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH,
            f"NTRADER_REAL_MONEY_ACCOUNT authorizes account {mask_account(env_account)}, but "
            "TWS_ACCOUNT is not set. The two declarations must name the same account exactly.",
        )

    if env_account != account:
        return build_refusal(
            GateRefusalReason.REAL_MONEY_ACCOUNT_MISMATCH,
            f"Authorized real-money account {mask_account(env_account)} does not match the "
            f"configured TWS_ACCOUNT {mask_account(account)}. The two declarations must name "
            "the same account exactly.",
        )

    return GateDecision(permitted=True, mode=GateMode.REAL_MONEY)


def _evaluate_paper(*, trading_mode: str, port: int, account: str) -> GateDecision:
    """Require all three paper conditions; an unrecognised port fails closed."""
    if trading_mode != "paper":
        return build_refusal(
            GateRefusalReason.NON_PAPER_TRADING_MODE,
            f"IBKR_TRADING_MODE is '{trading_mode}', not 'paper'. Paper trading is the only "
            "permitted mode without an explicit real-money authorization.",
        )

    if port not in PAPER_PORTS:
        permitted_ports = ", ".join(str(known) for known in sorted(PAPER_PORTS))
        return build_refusal(
            GateRefusalReason.NON_PAPER_PORT,
            f"IBKR_PORT {port} is not a known paper port ({permitted_ports}). Any other port "
            "is refused, including unrecognised ones.",
        )

    if account and not account.upper().startswith(PAPER_ACCOUNT_PREFIXES):
        paper_prefixes = "/".join(PAPER_ACCOUNT_PREFIXES)
        return build_refusal(
            GateRefusalReason.NON_PAPER_ACCOUNT_PREFIX,
            f"TWS_ACCOUNT {mask_account(account)} does not start with a paper account prefix "
            f"({paper_prefixes}), so it may be a real-money account.",
        )

    return GateDecision(permitted=True, mode=GateMode.PAPER)


def normalize_reported_accounts(reported_accounts: frozenset[str]) -> frozenset[str]:
    """Strip the gateway's account identifiers and drop the empty ones.

    Public because the *decision* and everything that *renders* the evidence must
    agree on what the evidence was. IBKR's ``managedAccounts`` payload is a
    comma-separated string, so a trailing comma or a padded entry yields a blank
    or whitespace-padded member — and an unstripped member masks to the wrong
    three characters (``"DU4076626 "`` → ``"*** "``), which would put a value in
    a log line that the gate never actually judged.
    """
    return frozenset(account.strip() for account in reported_accounts if account.strip())


def evaluate_account_gate(
    settings: "IBKRSettings",
    cli_flags: GateFlags,
    reported_accounts: frozenset[str],
) -> GateDecision:
    """Judge the accounts the connected gateway actually reported (Layer 2).

    Layer 1 checks what the configuration *claims*; this checks what the broker
    *says*. It exists because those can differ in three ways Layer 1 cannot see:
    a gateway that manages a real-money account alongside the configured paper
    one, a real-money authorization that names an account the gateway does not
    have, and a real-money authorization that names a demo account.

    Still pure — the caller supplies the reported identifiers. Obtaining them
    from a live node is ``src/core/live_account_gate.py``'s job.

    Args:
        settings: Loaded IBKR settings — the same object Layer 1 reads.
        cli_flags: Operator declarations from the command line.
        reported_accounts: The account identifiers the connected gateway named.
            Blank and whitespace-only entries are discarded; an empty result is
            a refusal, never a permit, because an unverifiable account is
            indistinguishable from a real-money one.

    Returns:
        Layer 1's decision verbatim whenever Layer 1 refused, otherwise a
        decision applying the reported-account rules for the permitted mode.
    """
    decision = evaluate_gate(settings, cli_flags)
    if not decision.permitted:
        # Returned unchanged, not merged: Layer 2 is a strictly additional
        # condition, so there is no path on which a clean reported account
        # rescues a configuration Layer 1 already refused.
        return decision

    reported = normalize_reported_accounts(reported_accounts)
    if not reported:
        return build_refusal(
            GateRefusalReason.ACCOUNT_NOT_REPORTED,
            "The connected gateway named no account, so there is nothing to verify against. "
            "Refusing rather than assuming the connection is paper.",
        )

    if decision.mode is GateMode.REAL_MONEY:
        return _evaluate_reported_real_money(
            authorized=settings.ntrader_real_money_account.strip(),
            reported=reported,
        )

    return _evaluate_reported_paper(reported)


def _evaluate_reported_paper(reported: frozenset[str]) -> GateDecision:
    """Require *every* reported account to be a paper account.

    Not "the configured one is paper" — that is Layer 1's check, and the IB
    execution client separately verifies the configured account is one the
    gateway manages. What neither catches is a gateway that also manages a
    real-money account: the session would then be one misconfiguration away
    from it, on a connection everything so far declared safe.
    """
    # `sorted(set(...))` — a `frozenset` has no stable iteration order, and two
    # distinct accounts can share their last three characters, which would
    # otherwise render as "***567, ***567" and leave the operator unable to tell
    # how many distinct accounts are at fault.
    non_paper = sorted(
        {
            mask_account(account)
            for account in reported
            # Upper-cased to mirror `_evaluate_paper` (live_gate.py's own paper
            # branch), which already permits a lowercase `du…`. Note what that
            # costs and why it is still right: on THIS path matching a paper
            # prefix *suppresses* a refusal, so folding is permit-widening, not
            # refusal-widening — the opposite polarity to the fold in
            # `_evaluate_reported_real_money`. It is kept because divergence
            # from Layer 1 would be the worse failure (a configuration Layer 1
            # permitted would be refused here for its casing alone), and because
            # a real IBKR gateway reports uppercase identifiers.
            if not account.upper().startswith(PAPER_ACCOUNT_PREFIXES)
        }
    )
    if non_paper:
        paper_prefixes = "/".join(PAPER_ACCOUNT_PREFIXES)
        return build_refusal(
            GateRefusalReason.REPORTED_ACCOUNT_NOT_PAPER,
            f"The connected gateway reports account(s) {', '.join(non_paper)} without a paper "
            f"account prefix ({paper_prefixes}). A paper port is not proof of a paper account, "
            "so the connection is refused before any strategy starts.",
        )

    return GateDecision(permitted=True, mode=GateMode.PAPER)


def _evaluate_reported_real_money(*, authorized: str, reported: frozenset[str]) -> GateDecision:
    """Check the authorized account against what the gateway reports.

    Only the authorized account is judged: a real-money gateway legitimately
    manages paper accounts too, and refusing over their presence would make the
    crossing unreachable.
    """
    if authorized.upper().startswith(PAPER_ACCOUNT_PREFIXES):
        paper_prefixes = "/".join(PAPER_ACCOUNT_PREFIXES)
        return build_refusal(
            GateRefusalReason.REPORTED_ACCOUNT_IS_PAPER,
            f"Real-money trading is authorized for account {mask_account(authorized)}, which "
            f"carries a paper account prefix ({paper_prefixes}). Orders would land on a demo "
            "account while the operator believes they are real. Unset "
            "NTRADER_REAL_MONEY_ACCOUNT and drop --real-money to run paper trading.",
        )

    # Compared exactly, not case-folded. `_evaluate_real_money_crossing` compares
    # its two declarations exactly, and folding here would make MORE accounts
    # match — on this path, "matching more" means permitting more. The prefix
    # test above deliberately does fold, because there folding can only refuse
    # more. The asymmetry is the point, not an oversight.
    if authorized not in reported:
        named = ", ".join(sorted({mask_account(account) for account in reported}))
        return build_refusal(
            GateRefusalReason.REPORTED_ACCOUNT_NOT_AUTHORIZED,
            f"The connected gateway does not report the authorized real-money account "
            f"{mask_account(authorized)} — it reports {named}. The authorization and the "
            "gateway must name the same account exactly.",
        )

    return GateDecision(permitted=True, mode=GateMode.REAL_MONEY)


def build_refusal(reason: GateRefusalReason, message: str) -> GateDecision:
    """Build a refusal decision, keeping the permitted/refusal invariant intact.

    Public because the Layer 2 enforcement seam
    (``src/core/live_account_gate.py``) also produces refusals, and this must
    stay the **only** constructor for one: a second module spelling out
    ``GateDecision(permitted=False, mode=None, refusal=...)`` by hand is how the
    invariant quietly acquires an exception.

    Refusals carry no mode: see :class:`GateDecision`.
    """
    return GateDecision(
        permitted=False,
        mode=None,
        refusal=GateRefusal(reason=reason, message=message),
    )
