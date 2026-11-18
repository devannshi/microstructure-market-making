"""Transparent quoting policies; no access to future tape events."""

import math
from enum import Enum

from .types import Calibration, ReplayConfig


class Policy(str, Enum):
    PASSIVE = "passive"
    AS_FIXED = "as_fixed"
    AS_ADAPTIVE = "as_adaptive"
    AS_IMBALANCE = "as_imbalance"


def quote_prices(
    policy: Policy | str,
    bid: float,
    ask: float,
    inventory: float,
    variance: float,
    remaining: float,
    calibration: Calibration,
    config: ReplayConfig,
    imbalance: float = 0.0,
) -> tuple[float, float]:
    """Return passive integer-tick prices; quantity limits belong to the engine.

    `remaining` is time to scheduled session end, known before replay. Variance
    uses observations already processed. Order-book imbalance is a heuristic
    extension, not an estimated alpha or a theorem about optimal quoting.
    """
    policy = Policy(policy)
    if policy == Policy.PASSIVE:
        return math.floor(bid + 1e-8), math.ceil(ask - 1e-8)
    sigma2 = calibration.variance_rate if policy == Policy.AS_FIXED else variance
    tau = min(max(remaining, 0.0), config.horizon)
    gamma = calibration.gamma
    risk = gamma * max(sigma2, 1e-10) * tau
    mid = 0.5 * (bid + ask)
    reservation = mid - inventory * risk
    if policy == Policy.AS_IMBALANCE:
        reservation += 0.5 * (ask - bid) * max(-1.0, min(1.0, imbalance))
    half_width = 0.5 * risk + math.log1p(gamma / calibration.kappa) / gamma
    proposed_bid = math.floor(reservation - half_width + 1e-10)
    proposed_ask = math.ceil(reservation + half_width - 1e-10)
    # One tick apart at minimum; never submit a marketable order.
    highest_passive_bid = math.ceil(ask - 1e-8) - 1
    lowest_passive_ask = math.floor(bid + 1e-8) + 1
    return max(1.0, min(proposed_bid, highest_passive_bid)), max(proposed_ask, lowest_passive_ask)
