"""Model prices in USD per million tokens, and what a call costs.

The spend caps cannot be enforced without a price, so a configured model with no
known price stops the worker at startup. Self-hosters can add or change prices with
LT_PRICE_OVERRIDES. Checked against the provider's pricing on PRICES_CHECKED_ON.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

PRICES_CHECKED_ON = "2026-10-06"
WEB_SEARCH_USD = Decimal("0.01")  # USD 10 per 1,000 searches
_PER_MTOK = Decimal(1_000_000)


@dataclass(frozen=True)
class Price:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    # Some models bill the whole request at a higher rate once the prompt is long.
    long_above_tokens: int | None = None
    long_input_per_mtok: Decimal | None = None
    long_output_per_mtok: Decimal | None = None

    def cost_usd(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> Decimal:
        """What a call costs. Fail-safe: where a rate is not known, overstate.

        Cache tokens count toward the long-context threshold, and until per-model
        cache prices are added they are charged at twice the highest input rate,
        which is at least the dearest cache-write premium.
        """
        cache_tokens = cache_read_tokens + cache_write_tokens
        rate_in, rate_out = self.input_per_mtok, self.output_per_mtok
        prompt_tokens = input_tokens + cache_tokens
        if self.long_above_tokens is not None and prompt_tokens > self.long_above_tokens:
            rate_in = self.long_input_per_mtok or rate_in
            rate_out = self.long_output_per_mtok or rate_out
        cache_rate = 2 * max(rate_in, self.input_per_mtok, self.long_input_per_mtok or 0)
        total = input_tokens * rate_in + output_tokens * rate_out + cache_tokens * cache_rate
        return total / _PER_MTOK


PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(
        Decimal("0.10"),
        Decimal("0.50"),
        long_above_tokens=100_000,
        long_input_per_mtok=Decimal("0.50"),
        long_output_per_mtok=Decimal("2.50"),
    ),
    "claude-sonnet-5-5": Price(Decimal("2"), Decimal("10")),
    "claude-opus-5-5": Price(Decimal("4"), Decimal("20")),
}


class UnknownPrice(Exception):
    pass


Overrides = Mapping[str, Mapping[str, Decimal]]


def price_for(model: str, overrides: Overrides) -> Price:
    """The model's price. An override sets the base rates but keeps the model's
    long-context tier, whose rates are never below the override's own."""
    if model in overrides:
        rate_in, rate_out = overrides[model]["in"], overrides[model]["out"]
        default = PRICES.get(model)
        if default is None or default.long_above_tokens is None:
            return Price(rate_in, rate_out)
        return Price(
            rate_in,
            rate_out,
            long_above_tokens=default.long_above_tokens,
            long_input_per_mtok=max(default.long_input_per_mtok or rate_in, rate_in),
            long_output_per_mtok=max(default.long_output_per_mtok or rate_out, rate_out),
        )
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownPrice(f"no known price for {model}") from None


def missing_prices(models_by_setting: Mapping[str, str], overrides: Overrides) -> list[str]:
    problems = []
    for setting, model in models_by_setting.items():
        if model not in overrides and model not in PRICES:
            problems.append(f"{setting}={model} has no known price; add it to LT_PRICE_OVERRIDES")
    return problems
