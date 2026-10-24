"""Prices: a dated table in code, overrides from config, and a cost function."""

from decimal import Decimal

import pytest

from learning_tracker.worker.models.prices import (
    PRICES,
    PRICES_CHECKED_ON,
    WEB_SEARCH_USD,
    Price,
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
    assert price_for("claude-haiku-5-5", overrides) == Price(Decimal("0.2"), Decimal("1"))
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
