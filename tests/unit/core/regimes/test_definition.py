"""Definitions: pinned canonical JSON and hash, strict keys, bar hash (tasks.md 1.6)."""

import pytest
from pydantic import ValidationError

from src.core.regimes.definition import (
    bar_hash,
    build_definition,
    canonical_json,
    default_definition,
    definition_hash,
    validate_labels,
)

#: Pinned. Changing a default parameter is a new calendar version, so it must be deliberate.
#: Ranging moved from ER < 0.3 (SPY v1) to ER < 0.2 (v2), Allay, 2026-11-27.
GROUND_TRUTH_JSON = (
    '{"annualisation":252,"atr_window":20,"efficiency_window":21,"label_set":"ground_truth",'
    '"ranging_threshold":0.2,"scales":{"intermediate":6.0,"major":12.0,"minor":3.0},'
    '"volatility_window":21}'
)
DETECTOR_JSON = '{"label_set":"detector","sma200":{"window":200},"warmup_sessions":272}'
GROUND_TRUTH_HASH = "45e4389e332739006caca00ab666ea26a7e540299795f3faf45050c69eaa8228"
#: The stored SPY ground-truth v1: the old default, now reached by this override.
V1_OVERRIDES = {"ranging_threshold": 0.3}
V1_HASH = "5fc1c01619ad028b96de07cb7f7eba2a889cf5f8b29aea0ddbea8530fd58fedd"
DETECTOR_HASH = "7e31564b6a1766127500eb2ac9fec90ed462a9a39a6138fb1277342938f6d17f"


def test_default_definitions_are_pinned():
    assert canonical_json(default_definition("ground_truth")) == GROUND_TRUTH_JSON
    assert canonical_json(default_definition("detector")) == DETECTOR_JSON
    assert definition_hash(default_definition("ground_truth")) == GROUND_TRUTH_HASH
    assert definition_hash(default_definition("detector")) == DETECTOR_HASH


def test_v1_definition_is_still_reachable_by_its_override():
    """Extending or re-verifying the stored v1 needs its exact hash, so it is pinned too."""
    v1 = build_definition("ground_truth", V1_OVERRIDES)
    assert definition_hash(v1) == V1_HASH
    assert v1.model_dump() == {**default_definition("ground_truth").model_dump(), **V1_OVERRIDES}


@pytest.mark.parametrize(
    ("label_set", "overrides"),
    [
        ("ground_truth", {"scales": {"major": 10}}),
        ("ground_truth", {"atr_window": 14}),
        ("ground_truth", {"ranging_threshold": 0.25}),
        ("ground_truth", {"volatility_window": 31}),
        ("detector", {"sma200": {"window": 150}}),
        ("detector", {"warmup_sessions": 300}),
    ],
)
def test_any_changed_parameter_changes_the_hash(label_set, overrides):
    changed = build_definition(label_set, overrides)
    assert definition_hash(changed) != definition_hash(default_definition(label_set))


def test_override_keeps_the_other_defaults():
    changed = build_definition("ground_truth", {"scales": {"major": 10}})
    assert changed.scales.minor == 3.0 and changed.scales.major == 10.0


@pytest.mark.parametrize(
    ("label_set", "overrides"),
    [
        ("ground_truth", {"bogus": 1}),
        ("ground_truth", {"scales": {"huge": 20}}),
        ("detector", {"sma50": {"window": 50}}),
        ("ground_truth", {"efficiency_window": 20}),  # centred windows are odd
        ("ground_truth", {"label_set": "detector"}),
    ],
)
def test_unknown_or_invalid_keys_are_refused(label_set, overrides):
    with pytest.raises(ValidationError):
        build_definition(label_set, overrides)


def test_definition_holds_no_data_derived_value():
    text = canonical_json(default_definition("ground_truth"))
    assert "median" not in text and "frozen" not in text
    # Built twice, with no data in sight: the hash cannot depend on bars.
    assert definition_hash(build_definition("ground_truth")) == GROUND_TRUTH_HASH


def test_bar_hash_is_stable_and_sensitive_to_each_price():
    base = bar_hash(100.0, 101.0, 99.0, 100.5)
    assert base == bar_hash(100, 101, 99, 100.5)
    assert len(base) == 32
    for changed in ((100.01, 101, 99, 100.5), (100, 101.01, 99, 100.5), (100, 101, 98.99, 100.5),
                    (100, 101, 99, 100.51)):  # fmt: skip
        assert bar_hash(*changed) != base


def test_label_payloads_are_validated():
    assert validate_labels("detector", {"sma200": "up"}) == {"sma200": "up"}
    with pytest.raises(ValidationError):
        validate_labels("detector", {"sma200": "sideways"})
    with pytest.raises(ValidationError):
        validate_labels("ground_truth", {"leg_minor": "up"})
