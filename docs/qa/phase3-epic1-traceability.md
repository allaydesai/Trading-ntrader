# Phase 3, Epic 1 — Acceptance Criteria Traceability

Maps every acceptance criterion in
[`_bmad-output/planning-artifacts/prd-epic1-scope.md`](../../_bmad-output/planning-artifacts/prd-epic1-scope.md)
to the automated test that evidences it.

**Why this file exists.** Epic 1's coverage is spread across three test tiers, and
the tiers run under three different commands (`make test-unit`,
`make test-component`, `make test-integration`). A verification run that executes
only one of them sees only that tier's output, and the criteria proven in the
other two look unevidenced. This is the index that makes the mapping checkable
without running everything and reading 483 test names.

**Reproducing the whole set:**

```bash
make test-unit          # Stories 1.1, 1.2, 1.6, 1.7
make test-component     # Stories 1.3, 1.4, 1.5, 1.7
make test-integration   # Story 1.3 (--forked is mandatory)
```

483 tests cover the criteria below. No test contacts a broker, a network or a
database (NFR32/NFR34); the broker-dependent behaviour that cannot be automated
is operator-verified in [`phase3-live-verification.md`](phase3-live-verification.md).

---

## Story 1.1 — Refuse a Real-Money Account Before Anything Connects

Tier: unit · `tests/unit/core/test_live_gate.py`

| Acceptance criterion | Evidence |
| --- | --- |
| `evaluate_gate()` returns a `GateDecision` without socket, file or database I/O (AR16) | `TestGatePurity::test_gate_reads_no_environment_variables` |
| The module imports nothing from `nautilus_trader`, SQLAlchemy or any I/O library | `TestGatePurity::test_module_imports_nothing_that_can_perform_io`, with 12 parametrised meta-cases in `test_the_purity_check_actually_rejects_impure_sources` proving the checker rejects impure sources (including function-level and `importlib` indirection) |
| Paper mode + port ∈ {7497, 4002} + empty/`DU`/`DF` account permits | `TestPaperPermits` (7 cases) |
| Any single failing condition refuses, naming that condition | `TestPaperRefusals::test_failing_condition_refuses_with_its_own_reason` (5 cases) |
| An unrecognised port refuses rather than defaulting to permitted (fail-closed) | `TestPaperRefusals::test_unrecognised_port_refuses_rather_than_defaulting_to_permitted` |
| Refusal precedence is the documented order | `TestRefusalPrecedence` (2 cases) |
| `--real-money` / `NTRADER_REAL_MONEY_ACCOUNT` — every incomplete or mismatched combination refuses | `TestRealMoneyCrossing::test_incomplete_or_mismatched_declarations_refuse` (6 cases: flag-without-env, env-without-flag, disagreeing, no-account, case-sensitivity) plus `test_stale_env_var_refuses_even_when_configuration_is_paper_clean` |
| Only both-present-and-exactly-matching permits (FR10, AR15) | `TestRealMoneyCrossing::test_both_declarations_matching_exactly_permits_real_money` |
| No interactive prompt on this path | `TestGatePurity::test_module_imports_nothing_that_can_perform_io` (a prompt requires I/O the module cannot reach) |
| Account identifiers masked to last 3 characters (NFR26) | `TestAccountMasking` (2), `TestMaskAccount` (14 cases incl. the short-account disclosure boundary) |

## Story 1.2 — Isolate the Live Client ID from the Historical Data Client

Tier: unit · `tests/unit/test_ibkr_config.py`

| Acceptance criterion | Evidence |
| --- | --- |
| `ibkr_live_client_id` exists, default `10`; `ibkr_client_id` keeps default `1` (FR5, AR18) | `TestIBKRClientIdIsolation::test_client_id_defaults_are_distinct` |
| Configurable via env | `test_live_client_id_loads_from_env` |
| Equal client ids raise a Pydantic validation error naming both fields | `test_equal_client_ids_are_rejected` (3 cases: either field overridden, and both) |
| Raised by a model validator, so it fires regardless of which field was overridden | the 3 cases above cover both directions; `test_env_driven_collision_is_rejected` covers the env path |
| Field descriptions state the reservation (historical / live / reconcile = live+1, AR34) | `test_reservation_is_documented_in_field_metadata` |
| The historical rotation range may not reach the reserved ids | `test_historical_rotation_range_may_not_reach_the_reserved_ids` (4 cases), `test_live_id_may_not_sit_inside_the_historical_rotation_range` |

## Story 1.3 — Assemble and Start a TradingNode Against IBKR Paper

Tiers: component · `tests/component/core/test_live_node_builder.py`,
`tests/component/core/test_live_dependency_invariance.py` — integration ·
`tests/integration/core/test_live_node_lifecycle.py`

| Acceptance criterion | Evidence |
| --- | --- |
| `TradingNodeConfig` built from `InteractiveBrokersDataClientConfig` / `ExecClientConfig` (FR2, AR2) | `TestConfigShape::test_permitted_build_returns_configured_ib_clients`, `test_every_setting_lands_on_the_config_it_belongs_to` |
| Both live factories registered | `test_live_node_lifecycle.py::_assert_factories_registered`, asserted in all 3 node-building integration tests (Nautilus logs-and-continues on a missing registration, so this is asserted rather than assumed) |
| The builder calls `evaluate_gate()` first and raises before constructing any client config (FR9) | `TestGateOrdering::test_refusing_configuration_raises_gate_refused_error_with_refusal`, `test_no_client_config_constructed_before_gate_runs`, and `test_the_sentinels_must_be_able_to_fire` (meta-test proving the sentinels can fail) |
| `ibkr_read_only=False` scoped to the process only, never read as a safety control (FR12, NFR27, AR17, AR43) | `TestReadOnlyDerivedView` (2), `TestReadOnlyNeverBranchedOn::test_ibkr_read_only_appears_exactly_once_and_never_in_a_boolean_test` with 9 parametrised meta-cases proving the AST guard catches every branch form (`if`/`while`/`assert`/`not`/boolop/ternary/comprehension/`match`/match-guard) |
| Log guard registered via `set_nautilus_log_guard()` before any other Nautilus component (FR7, AR20) | `TestLogGuardRegistrationOnNodeFirst::test_node_first_registers_its_own_log_guard` |
| A node built under `--forked` in a process that already ran a `BacktestEngine` shows no C-logging double-init panic | `TestCoexistenceWithBacktestEngine::test_node_builds_without_panic_after_a_backtest_engine_claimed_logging` |
| Both data and execution clients use `ibkr_live_client_id`, not `ibkr_client_id` (FR5) | `TestClientId::test_both_clients_use_the_live_client_id_not_the_historical_one` (2 cases); observable end-to-end in `test_live_check_driver.py::TestStructuredLogging::test_the_live_client_id_is_the_one_that_reaches_the_logs` |
| Shutdown leaks no running event loop or unclosed connection | `test_live_check_driver.py::TestShutdownDiscipline::test_the_event_loop_is_always_closed` and `test_the_event_loop_is_closed_on_the_success_path_too` — both assert on the loop the driver actually created, captured from the `loop=` kwarg |
| A second node is buildable in a fresh process, without inheriting `BacktestEngine`'s single-use constraint | `TestShutdownLeavesNothingBehind::test_a_second_node_is_buildable_after_the_first_shuts_down` (subprocess probe; the `timeout` is the assertion for "the process exits"), with `test_the_probe_can_actually_fail` as its meta-test |
| No new dependency added — `pyproject.toml`/`uv.lock` unchanged (AR3) | `test_live_dependency_invariance.py::TestNoDependencyWasAdded::test_every_third_party_import_is_already_declared`, plus `test_the_check_would_catch_an_undeclared_import` (meta-test) and `test_ibapi_is_allowed_only_because_a_declared_extra_supplies_it` |
| The IB adapter ships with the installed `nautilus-trader` 1.220.0 | `TestTheAdapterShipsWithNautilus` (2) |

## Story 1.4 — Verify the Connected Account Is a Paper Account Before Trading Starts

Tiers: component · `tests/component/core/test_live_account_gate.py` — unit ·
`tests/unit/core/test_live_gate.py` (the Layer 2 decision function is pure)

| Acceptance criterion | Evidence |
| --- | --- |
| The gateway-reported account must carry a `DU`/`DF` prefix at `gate:account` for the sequence to continue (FR8, AR14) | `TestAccountGatePaperPath` (6 cases), `test_one_non_paper_account_refuses_the_whole_connection` |
| Absent or blank reported accounts fail closed | `TestAccountGateFailsClosedWithoutEvidence` (4 cases) |
| A non-paper account shuts the node down immediately, starts no strategy, and produces the same refusal outcome as the static gate (FR9) | `TestAccountGateLayerOneDominance::test_layer_one_refusal_is_returned_unchanged` (6 cases), `test_live_check_driver.py::TestShutdownDiscipline::test_a_layer_two_refusal_still_disposes_the_node` |
| A failing shutdown does not mask the refusal | `test_live_account_gate.py::test_a_failing_shutdown_does_not_mask_the_refusal` |
| The account identifier is masked to its last 3 characters in verification logs (NFR26) | `TestAccountGateMasking` (6 cases), `TestAccountGatePhaseLogging::test_no_log_record_ever_carries_a_full_account` (2 cases), and `test_distinct_accounts_sharing_a_masked_suffix_are_not_collapsed_silently` |
| Verification runs after connection and strictly before any strategy starts (AR39) | `TestAccountGatePhaseLogging::test_permit_logs_started_then_ok` / `test_refusal_logs_started_then_failed_with_the_reason` pin the phase ordering; Epic 1 starts no strategy by design |

## Story 1.5 — Receive Real-Time RTH Bars for a Configured Instrument

Tier: component · `tests/component/core/test_live_market_data.py`,
`test_live_bar_observer.py`, `test_live_node_builder.py` — integration ·
`tests/integration/core/test_live_node_lifecycle.py`

| Acceptance criterion | Evidence |
| --- | --- |
| Market data type is `REALTIME`, overriding the `DELAYED_FROZEN` default (FR3, AR21) | `TestMarketDataTypeResolution::test_unset_market_data_type_is_overridden_to_realtime`, `test_explicit_realtime_is_accepted_in_any_casing` (3 cases); `TestMarketDataConfiguration::test_data_client_requests_realtime_not_the_fetch_default` |
| A session that cannot obtain real-time data fails loudly rather than falling back | `TestMarketDataConfiguration::test_an_explicit_delayed_configuration_is_refused`; `test_live_bar_observer.py::TestObserverDelayedDataGuard` (10 cases, incl. `test_the_real_shutdown_command_reaches_the_message_bus` and the latch that makes one downgrade one shutdown) |
| Bars restricted to RTH via `ibkr_use_rth=True` (FR4, AR21) | `test_data_client_restricts_bars_to_regular_trading_hours`, `test_disabling_rth_is_refused`, `test_both_fields_are_passed_explicitly_not_left_to_adapter_defaults` (+ `test_the_explicitness_guard_can_fail` as its meta-test) |
| A closed bar is delivered to the node and logged with instrument and timestamp (FR1) | `TestObserverBarDelivery::test_the_log_line_carries_the_instrument_and_the_bar_timestamp`, `test_a_delivered_bar_is_counted`, `test_counts_are_kept_per_subscription`; wiring proven end-to-end by `TestBarObserverReachesTheNode::test_a_configured_observer_is_instantiated_and_registered_on_the_trader` (integration — the kernel, not the builder, resolves the actor's dotted paths) |
| Instrument count over the market-data-line budget fails at startup naming limit and count (NFR16, NFR30) | `TestMarketDataLineBudgetAtStartup::test_exceeding_the_budget_fails_at_startup_naming_limit_and_count`, `test_the_budget_check_runs_before_any_client_config_is_constructed`, `test_the_gate_still_runs_first`; the setting itself in `test_ibkr_config.py::TestMarketDataLineBudget` (5 cases) |
| The existing 45 req/s pacing discipline governs requests (NFR15) | `TestObserverPacing` (14 cases: `test_an_oversized_set_is_dispatched_one_batch_per_second`, `test_the_default_rate_is_the_existing_forty_five`, `test_rate_is_sourced_from_settings_not_a_second_literal`, non-positive-rate refusals); propagation asserted in `test_live_node_builder.py::test_an_observer_config_becomes_an_importable_actor_config` (`requests_per_second == 45`) |

## Story 1.6 — Detect Connection Loss and Withhold Trading Permission

Tier: unit · `tests/unit/core/test_live_connection_monitor.py` — component ·
`tests/component/core/test_live_connection_probe.py`

| Acceptance criterion | Evidence |
| --- | --- |
| A drop logs `connection.lost` with the session identifier bound, and trading-permitted becomes false (FR6, AR41) | `TestConnectionLost::test_drop_withdraws_permission_and_logs_with_session_bound`, `test_repeated_disconnected_polls_log_once`, `test_loss_from_recovering_does_not_re_log_the_same_outage` |
| Permission is granted only while connected | `TestPermissionInvariant` (5 states + `test_driver_covers_every_state` + `test_permission_has_no_setter`), `TestHalt::test_no_disconnected_observation_ever_permits_trading` |
| `connection.restored` is logged and permission restored only after state is re-established, never on reconnect alone (NFR10) | `TestRecovery::test_reconnect_alone_does_not_restore_permission`, `test_confirmation_restores_permission_and_logs_once`, `test_confirmation_carries_its_own_reading_and_refuses_a_dead_socket`, `test_confirmation_is_refused_while_the_source_is_not_connected` (3 cases) |
| Reconnection completes within 60s with elapsed time observable in the logs (NFR4) | `TestReconnectTiming` (8 cases: `test_reconnect_and_downtime_are_reported_separately`, `test_a_slow_reconnect_is_reported_not_suppressed`, `test_a_backward_clock_step_cannot_defeat_the_halt`); the 60s default in `TestConstructorValidation::test_defaults_are_the_nfr_targets` |
| The daily gateway restart is an expected event, not an error (NFR19) | `TestScheduledGatewayRestart::test_a_full_cycle_raises_nothing_and_logs_at_expected_levels`, `test_repeated_restart_cycles_return_the_monitor_to_the_same_state` |
| Unreachable beyond the reconnect window halts trading and reports, never proceeding on stale state (NFR20) | `TestHalt` (8 cases: `test_outage_past_the_window_halts_and_reports_once`, `test_a_flapping_connection_cannot_reset_the_halt_clock`, `test_an_unconfirmed_recovery_still_halts`, `test_a_confirmation_cannot_outrun_the_halt_deadline`); staleness in `TestPermissionExpiresWhenObservationsStop` (4 cases) |
| The monitor is pure — no framework or I/O import | `TestModulePurity::test_module_imports_no_framework_or_io_library` |

## Story 1.7 — Check Broker Connectivity and the Gate from the CLI

Tiers: unit · `tests/unit/cli/commands/test_live_cli.py`,
`tests/unit/core/test_live_check.py` — component ·
`tests/component/core/test_live_check_driver.py`

| Acceptance criterion | Evidence |
| --- | --- |
| `ntrader live check` evaluates the gate, connects, verifies the account, subscribes, prints bars, disconnects, exits 0 (FR1–FR5) | `test_live_check_driver.py::TestHappyPath::test_a_complete_check_reports_ok_and_exit_zero`; `test_live_cli.py::TestExitCodes::test_a_successful_check_exits_zero` |
| A refused configuration prints the specific reason and exits 3 (FR11, AR28) | `TestExitCodes::test_a_gate_refusal_exits_three`, `test_a_gate_refusal_prints_the_reason_and_the_message` |
| No connection attempt appears in the logs on the refusal path | `run_live_check` returns before any construction (`live_check_driver.py:132-137`); asserted by `test_live_check_driver.py::TestGateRefusal` and by the builder's `TestGateOrdering::test_no_client_config_constructed_before_gate_runs` |
| Gate passes but broker unreachable exits 4 | `TestExitCodes::test_an_unreachable_broker_exits_four`, `test_three_and_four_are_the_scriptable_distinction` |
| Other failures exit 1; usage errors exit 2 | `test_other_failures_exit_one` (3 cases), `TestUsageErrors::test_bad_input_exits_two_without_reaching_the_driver` (4 cases) |
| Structured `structlog` console output, consistent with existing CLI commands (FR48) | `test_live_check_driver.py::TestStructuredLogging` — `test_the_check_emits_structured_events_with_separate_fields` (asserts `gate.static`, `live_check.building`, `live_check.observing` with `host`/`port`/`client_id`/`trader_id` as separate fields), `test_no_streamed_event_carries_a_full_account_identifier`; wiring in `test_live_cli.py::TestConsoleOutputIsConsistentWithTheOtherGroups` (4) |
| `check` is listed under `ntrader live --help`, group wired into `src/cli/main.py` | `TestGroupRegistration` (3 cases) |
| No `--real-money` surface on this command | `TestNoRealMoneySurface` (3 cases) |

---

## Criteria deliberately not automated

Per the PRD's Testability NFRs, broker-dependent behaviour is operator-verified
against the real paper account and recorded in
[`phase3-live-verification.md`](phase3-live-verification.md). No automated test
calls `node.build()` or `node.run()`, because `build()` runs the IB factories and
`get_cached_ib_client` opens a socket to the gateway.
