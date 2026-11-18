"""Regime-switching marked-event generator with directional adverse selection.

Not an exchange simulator: observations are exogenous to the test market maker.
The future drift component correlates with aggressive trade sign; a price change
is emitted after the trade, preventing the policy from observing that response.
"""

from dataclasses import dataclass

import numpy as np

from .types import Event, Tape


@dataclass(frozen=True)
class SyntheticConfig:
    duration: float = 600.0
    tick_size: float = 0.01
    start_price_ticks: float = 10000.0
    quote_interval: float = 0.25
    trade_rate: float = 3.0
    low_sigma: float = 0.7
    high_sigma: float = 2.0
    switch_probability: float = 0.0015
    adverse_selection: float = 0.25
    mean_trade_size: float = 60.0
    displayed_depth: float = 30.0


def generate(seed: int, config: SyntheticConfig = SyntheticConfig()) -> Tape:
    if config.duration <= 0 or config.quote_interval <= 0 or config.trade_rate <= 0:
        raise ValueError("duration, quote interval and trade rate must be positive")
    if (
        min(config.low_sigma, config.high_sigma, config.mean_trade_size, config.displayed_depth)
        <= 0
    ):
        raise ValueError("volatility, sizes and depth must be positive")
    if not 0 <= config.switch_probability <= 1 or config.adverse_selection < 0:
        raise ValueError("invalid regime or adverse-selection parameter")
    rng = np.random.default_rng(seed)
    mid = config.start_price_ticks
    regime = 0
    events = []
    half = 1
    bid = np.floor(mid - half)
    ask = np.ceil(mid + half)
    events.append(Event(0, "quote", bid, ask, config.displayed_depth, config.displayed_depth))
    t = 0.0
    while t < config.duration:
        dt = min(config.quote_interval, config.duration - t)
        if rng.random() < config.switch_probability:
            regime = 1 - regime
        sigma = config.high_sigma if regime else config.low_sigma
        imbalance = np.tanh(rng.normal(0, 0.8))
        signed_flow = 0.0
        n = rng.poisson(config.trade_rate * dt)
        trade_times = np.sort(rng.uniform(t + 1e-7, t + dt - 1e-7, n))
        for trade_time in trade_times:
            side = 1 if rng.random() < 0.5 + 0.15 * imbalance else -1
            penetration = rng.geometric(0.65) - 1
            price = ask + penetration if side == 1 else bid - penetration
            size = max(1.0, float(np.floor(rng.exponential(config.mean_trade_size))))
            events.append(Event(float(trade_time), "trade", price=price, size=size, side=side))
            signed_flow += side
        # The signed-flow response is only visible after orders could have filled.
        mid += sigma * np.sqrt(dt) * rng.normal() + config.adverse_selection * signed_flow
        mid = max(mid, 100.0)
        t += dt
        half = 1 + regime
        bid, ask = np.floor(mid - half), np.ceil(mid + half)
        depth = config.displayed_depth
        events.append(
            Event(
                t,
                "quote",
                bid,
                ask,
                max(1.0, np.floor(depth * (1 + imbalance))),
                max(1.0, np.floor(depth * (1 - imbalance))),
            )
        )
    return Tape(events, f"synthetic-{seed}", config.tick_size, 1.0, "synthetic", config.duration)
