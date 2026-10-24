"""Prices: a dated table in code, overrides from config, and a cost function."""

from decimal import Decimal

import pytest

from learning_tracker.worker.models.prices import (
    PRICES,
    PRICES_CHECKED_ON,
    WEB_SEARCH_USD,
    UnknownPrice,
    missing_prices,
    price_for,
)


def test_the_default_models_have_documented_prices() -> None:
    assert PRICES_CHECKED_ON == "2026-10-06"
    haiku = PRICES["claude-haiku-5-5"]
    assert (haiku.input_per_mtok, haiku.output_per_mtok) == (Decimal("0.10"), Decimal("0.50"))
    sonnet = PRICES["claude-sonnet-5-5"]
    assert (sonnet.input_per_mtok, sonnet.output_per_mtok) == (Decimal("2"), Decimal("10"))
    assert Decimal("0.01") == WEB_SEARCH_USD  # USD 10 per 1,000 searches


def test_cost_is_exact_decimal_dollars() -> None:
    sonnet = PRICES["claude-sonnet-5-5"]
    assert sonnet.cost_usd(input_tokens=24_000, output_tokens=700) == Decimal("0.055")


def test_haiku_charges_its_higher_rate_above_100k_input_tokens() -> None:
    haiku = PRICES["claude-haiku-5-5"]
    assert haiku.cost_usd(input_tokens=100_000, output_tokens=0) == Decimal("0.01")
    assert haiku.cost_usd(input_tokens=100_001, output_tokens=1_000) == Decimal("0.0525005")


def test_overrides_win_and_can_add_a_model() -> None:
    overrides = {
        "claude-haiku-5-5": {"in": Decimal("0.2"), "out": Decimal("1")},
        "local-model": {"in": Decimal("0"), "out": Decimal("0")},
    }
    haiku = price_for("claude-haiku-5-5", overrides)
    assert (haiku.input_per_mtok, haiku.output_per_mtok) == (Decimal("0.2"), Decimal("1"))
    assert price_for("local-model", overrides).cost_usd(input_tokens=10, output_tokens=10) == 0


def test_a_model_without_a_price_cannot_be_used() -> None:
    with pytest.raises(UnknownPrice, match="claude-unknown-9"):
        price_for("claude-unknown-9", {})


def test_missing_prices_names_each_setting_that_points_at_an_unpriced_model() -> None:
    models = {"LT_MODEL_SCREEN": "claude-haiku-5-5", "LT_MODEL_CHOOSE": "claude-unknown-9"}
    assert missing_prices(models, {}) == [
        "LT_MODEL_CHOOSE=claude-unknown-9 has no known price; add it to LT_PRICE_OVERRIDES"
    ]
    assert missing_prices(models, {"claude-unknown-9": {"in": Decimal(1), "out": Decimal(2)}}) == []


# --- fail-safe: an unknown or partly known cost must overstate, never understate ---


def test_cache_tokens_are_charged_at_twice_the_highest_input_rate() -> None:
    sonnet = PRICES["claude-sonnet-5-5"]  # input 2 per MTok, no long tier
    cost = sonnet.cost_usd(input_tokens=0, output_tokens=0, cache_read_tokens=1_000)
    assert cost == Decimal("0.004")  # 1,000 x 2 x USD 2 per MTok
    assert sonnet.cost_usd(input_tokens=0, output_tokens=0, cache_write_tokens=1_000) == cost
    assert cost > sonnet.cost_usd(input_tokens=1_000, output_tokens=0)  # never cheaper


def test_cache_tokens_count_toward_the_long_context_threshold() -> None:
    haiku = PRICES["claude-haiku-5-5"]
    short = haiku.cost_usd(input_tokens=60_000, output_tokens=0)
    assert short == Decimal("0.006")  # precondition: 60K alone is below 100K
    cost = haiku.cost_usd(input_tokens=60_000, output_tokens=1_000, cache_read_tokens=50_000)
    # 110K prompt tokens: the long rates apply to everything, and cache at 2 x 0.50
    assert cost == Decimal("0.03") + Decimal("0.0025") + Decimal("0.05")


def test_an_override_keeps_the_long_context_tier() -> None:
    cheap = price_for(
        "claude-haiku-5-5", {"claude-haiku-5-5": {"in": Decimal("0.2"), "out": Decimal("1")}}
    )
    assert cheap.long_above_tokens == 100_000
    assert cheap.cost_usd(input_tokens=100_001, output_tokens=0) == PRICES[
        "claude-haiku-5-5"
    ].cost_usd(input_tokens=100_001, output_tokens=0)  # default long rate kept


def test_an_override_above_the_default_long_rate_is_never_undercut_by_it() -> None:
    dear = price_for(
        "claude-haiku-5-5", {"claude-haiku-5-5": {"in": Decimal("1"), "out": Decimal("5")}}
    )
    short = dear.cost_usd(input_tokens=100_000, output_tokens=1_000)
    long = dear.cost_usd(input_tokens=100_001, output_tokens=1_000)
    assert long >= short  # a longer prompt never looks cheaper
    assert long == Decimal("0.105001")  # long rates become the override's 1 and 5
