"""Event-driven passive execution with delayed cancel/replace and self-financing cash."""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .policies import Policy, quote_prices
from .types import Calibration, ReplayConfig, Tape


@dataclass(slots=True)
class Order:
    side: int  # our buy +1 / sell -1
    price: float
    remaining: float
    ahead: float
    activated: float
    submitted: float


@dataclass
class ReplayResult:
    summary: dict
    trace: pd.DataFrame
    fills: pd.DataFrame


FILL_COLUMNS = [
    "time",
    "side",
    "price",
    "size",
    "fee",
    "mid",
    "inventory",
    "cash",
    "activated",
    "submitted",
    "trade_size",
    "kind",
]


def replay(
    tape: Tape,
    policy: Policy | str,
    calibration: Calibration,
    config: ReplayConfig | None = None,
    record: bool = True,
) -> ReplayResult:
    """Replay a single session from flat inventory; forcibly liquidate at the end.

    On each event: activate older requests; match an eligible trade; update the
    observed book; observe volatility; schedule the next replacement. New orders
    cannot fill at their activation timestamp. All requests are atomic pair
    replacements; the old pair remains active during latency. At most one request
    may be pending. No price-impact feedback or queue cancellation credit is used.
    """
    config = ReplayConfig() if config is None else config
    policy = Policy(policy)
    first = tape.events[0]
    bid, ask, bid_size, ask_size = first.bid, first.ask, first.bid_size, first.ask_size
    mid = (bid + ask) / 2
    cash = inventory = fees = volume = traded_notional = spread_capture = 0.0
    bid_order = ask_order = None
    pending = None
    last_submit = -np.inf
    last_time = first.time
    end = tape.session_end
    variance = calibration.variance_rate
    var_mid, var_time = mid, first.time
    risk_integral = abs_integral = 0.0
    max_inventory = max_drawdown = 0.0
    peak_equity = 0.0
    n_fills = n_quotes = rejected = 0
    fill_rows, trace_rows = [], []
    next_trace = first.time

    def order(side, price, quantity, when, submitted, old):
        nonlocal rejected
        # Risk limit is checked again at acknowledgement, after latency-period fills.
        capacity = config.max_inventory - side * inventory
        quantity = math.floor(min(quantity, max(0.0, capacity)) + 1e-10)
        if (
            config.retain_priority
            and old is not None
            and old.price == price
            and old.remaining <= capacity
            and old.remaining > 0
        ):
            return old
        crosses = price >= ask if side == 1 else price <= bid
        if quantity <= 0 or crosses:
            rejected += int(crosses)
            return None
        at_or_behind = price <= bid if side == 1 else price >= ask
        ahead = (bid_size if side == 1 else ask_size) * config.queue_multiplier
        if config.queue == "front" or not at_or_behind:
            ahead = 0.0
        return Order(side, price, quantity, ahead, when, submitted)

    for event in tape.events:
        dt = event.time - last_time
        risk_integral += inventory * inventory * dt
        abs_integral += abs(inventory) * dt
        last_time = event.time

        if pending is not None and pending[0] <= event.time:
            arrival, submitted, pb, pa = pending
            bid_order = order(1, pb, config.order_size, arrival, submitted, bid_order)
            ask_order = order(-1, pa, config.order_size, arrival, submitted, ask_order)
            pending = None

        if event.kind == "trade":
            resting = ask_order if event.side == 1 else bid_order
            if resting is not None and resting.activated < event.time:
                reachable = (
                    event.price >= resting.price
                    if resting.side == -1
                    else event.price <= resting.price
                )
                if reachable:
                    # Trade-through is not treated as an unlimited free fill.
                    # We retain the conservative visible-depth proxy even when
                    # the reported trade is beyond our limit.
                    eaten = min(resting.ahead, event.size)
                    resting.ahead -= eaten
                    available = max(0.0, event.size - eaten)
                    capacity = config.max_inventory - resting.side * inventory
                    qty = math.floor(
                        min(
                            resting.remaining,
                            available,
                            event.size * config.participation,
                            capacity,
                        )
                        + 1e-10
                    )
                    if qty > 0:
                        fee = abs(resting.price * qty) * config.maker_fee_bps / 10000
                        cash -= resting.side * resting.price * qty + fee
                        inventory += resting.side * qty
                        fees += fee
                        volume += qty
                        traded_notional += resting.price * qty
                        spread_capture += resting.side * qty * (mid - resting.price)
                        resting.remaining -= qty
                        n_fills += 1
                        max_inventory = max(max_inventory, abs(inventory))
                        if record:
                            fill_rows.append(
                                [
                                    event.time,
                                    resting.side,
                                    resting.price,
                                    qty,
                                    fee,
                                    mid,
                                    inventory,
                                    cash,
                                    resting.activated,
                                    resting.submitted,
                                    event.size,
                                    "maker",
                                ]
                            )
                        if resting.remaining < 1e-9:
                            if resting.side == 1:
                                bid_order = None
                            else:
                                ask_order = None
        else:
            bid, ask, bid_size, ask_size = (event.bid, event.ask, event.bid_size, event.ask_size)
            mid = 0.5 * (bid + ask)
            elapsed = event.time - var_time
            if elapsed >= config.variance_sample:
                weight = -math.expm1(-math.log(2) * elapsed / config.variance_half_life)
                variance = max(
                    1e-8, (1 - weight) * variance + weight * (mid - var_mid) ** 2 / elapsed
                )
                var_mid, var_time = mid, event.time

        equity = cash + inventory * mid
        peak_equity = max(peak_equity, equity)
        max_drawdown = max(max_drawdown, peak_equity - equity)
        if record and event.time >= next_trace:
            trace_rows.append(
                [
                    event.time,
                    mid,
                    cash,
                    inventory,
                    equity,
                    variance,
                    bid_order.price if bid_order else np.nan,
                    ask_order.price if ask_order else np.nan,
                ]
            )
            next_trace = event.time + config.trace_interval
        if pending is None and event.time - last_submit >= config.refresh and event.time < end:
            imbalance = (bid_size - ask_size) / max(bid_size + ask_size, 1e-10)
            pb, pa = quote_prices(
                policy,
                bid,
                ask,
                inventory,
                variance,
                end - event.time,
                calibration,
                config,
                imbalance,
            )
            pending = (event.time + config.latency, event.time, pb, pa)
            last_submit = event.time
            n_quotes += 1

    risk_integral += inventory * inventory * (end - last_time)
    abs_integral += abs(inventory) * (end - last_time)
    terminal_inventory = inventory
    mtm_pnl = cash + inventory * mid
    liquidation_spread_cost = abs(inventory) * (ask - bid) / 2
    liquidation_fee = 0.0
    liquidation_size = abs(inventory)
    # Terminal closing auction proxy: liquidate the small remaining inventory at
    # the final opposite BBO plus taker fee. Report when visible depth is exceeded.
    liquidation_depth = bid_size if inventory > 0 else ask_size
    depth_exceeded = liquidation_size > liquidation_depth
    if liquidation_size:
        side = -1 if inventory > 0 else 1
        price = bid if inventory > 0 else ask
        liquidation_fee = liquidation_size * price * config.taker_fee_bps / 10000
        cash -= side * price * liquidation_size + liquidation_fee
        fees += liquidation_fee
        inventory = 0.0
        if record:
            fill_rows.append(
                [
                    end,
                    side,
                    price,
                    liquidation_size,
                    liquidation_fee,
                    mid,
                    0.0,
                    cash,
                    np.nan,
                    np.nan,
                    np.nan,
                    "liquidation",
                ]
            )
    max_drawdown = max(max_drawdown, peak_equity - cash)
    if record:
        trace_rows.append([end, mid, cash, 0.0, cash, variance, np.nan, np.nan])
    duration = max(tape.duration, 1e-12)
    summary = {
        "policy": policy.value,
        "tape": tape.name,
        "source": tape.source,
        "duration_s": tape.duration,
        "events": len(tape.events),
        "net_pnl_ticks_lots": cash,
        "mtm_pnl_ticks_lots": mtm_pnl,
        "net_pnl_quote_currency": cash * tape.tick_size * tape.lot_size,
        "gross_mtm_ticks_lots": mtm_pnl + fees - liquidation_fee,
        "fees_ticks_lots": fees,
        "maker_volume_lots": volume,
        "spread_capture_ticks_lots": spread_capture,
        "inventory_revaluation_ticks_lots": mtm_pnl + fees - liquidation_fee - spread_capture,
        "maker_notional_ticks_lots": traded_notional,
        "maker_fills": n_fills,
        "maker_notional_quote_currency": traded_notional * tape.tick_size * tape.lot_size,
        "quote_requests": n_quotes,
        "post_only_rejections": rejected,
        "max_abs_inventory": max_inventory,
        "terminal_inventory_before_close": terminal_inventory,
        "rms_inventory": math.sqrt(risk_integral / duration),
        "mean_abs_inventory": abs_integral / duration,
        "max_drawdown_ticks_lots": max_drawdown,
        "liquidation_spread_cost": liquidation_spread_cost,
        "liquidation_fee": liquidation_fee,
        "liquidation_exceeds_visible_depth": bool(depth_exceeded),
        "pnl_bps_maker_turnover": cash / traded_notional * 10000 if traded_notional > 0 else np.nan,
    }
    return ReplayResult(
        summary,
        pd.DataFrame(
            trace_rows,
            columns=[
                "time",
                "mid",
                "cash",
                "inventory",
                "equity",
                "variance",
                "bid_order",
                "ask_order",
            ],
        ),
        pd.DataFrame(fill_rows, columns=FILL_COLUMNS),
    )
