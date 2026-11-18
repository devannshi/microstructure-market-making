from dataclasses import replace

import numpy as np
import pytest

from microstructure_mm import Calibration, Event, ReplayConfig, Tape, replay
from microstructure_mm.policies import Policy, quote_prices

C = Calibration(1, 1, 1, 1, 0.01, 3, 3, 0, 1)
BASE = ReplayConfig(
    latency=0,
    refresh=100,
    maker_fee_bps=0,
    taker_fee_bps=0,
    order_size=3,
    max_inventory=10,
    participation=1,
    queue="front",
)


def tape(*events):
    return Tape([Event(0, "quote", 100, 102, 5, 5), *events])


def sell(t, price=100, size=10):
    return Event(t, "trade", price=price, size=size, side=-1)


def buy(t, price=102, size=10):
    return Event(t, "trade", price=price, size=size, side=1)


def close(t=3, bid=100, ask=102):
    return Event(t, "quote", bid, ask, 100, 100)


def test_round_trip_cash_is_exact():
    result = replay(tape(sell(1), buy(2), close()), "passive", C, BASE)
    assert result.summary["net_pnl_ticks_lots"] == 6
    assert result.summary["terminal_inventory_before_close"] == 0
    assert result.summary["maker_volume_lots"] == 6


def test_forced_liquidation_is_not_free_midprice():
    result = replay(tape(sell(1), close()), "passive", C, BASE)
    assert result.summary["mtm_pnl_ticks_lots"] == 3
    assert result.summary["liquidation_spread_cost"] == 3
    assert result.summary["net_pnl_ticks_lots"] == 0
    assert result.trace.iloc[-1].inventory == 0


def test_exact_maker_and_taker_fees():
    cfg = replace(BASE, maker_fee_bps=10, taker_fee_bps=20)
    r = replay(tape(sell(1), close()), "passive", C, cfg)
    assert r.summary["fees_ticks_lots"] == pytest.approx(0.9)
    assert r.summary["net_pnl_ticks_lots"] == pytest.approx(-0.9)


def test_rebate_has_correct_sign():
    r = replay(tape(sell(1), buy(2), close()), "passive", C, replace(BASE, maker_fee_bps=-10))
    assert r.summary["net_pnl_ticks_lots"] == pytest.approx(6.606)


def test_no_same_timestamp_fill():
    r = replay(tape(sell(0), close()), "passive", C, BASE)
    assert r.summary["maker_fills"] == 0


def test_no_fill_at_exact_activation_time():
    r = replay(tape(sell(1), close()), "passive", C, replace(BASE, latency=1))
    assert r.summary["maker_fills"] == 0


def test_latency_prevents_early_trade():
    r = replay(tape(sell(0.5), sell(1.5), close()), "passive", C, replace(BASE, latency=1))
    makers = r.fills.query("kind == 'maker'")
    assert list(makers.time) == [1.5]


def test_partial_fill_volume_conservation():
    r = replay(tape(sell(1, size=2), sell(2, size=2), close()), "passive", C, BASE)
    assert list(r.fills.query("kind == 'maker'")["size"]) == [2, 1]


def test_queue_consumed_before_our_order():
    cfg = replace(BASE, queue="back")
    r = replay(tape(sell(1, size=4), sell(2, size=3), close()), "passive", C, cfg)
    makers = r.fills.query("kind == 'maker'")
    assert list(makers.time) == [2]
    assert list(makers["size"]) == [2]


def test_no_cancellation_credit_from_book_size_drop():
    cfg = replace(BASE, queue="back")
    r = replay(
        tape(Event(0.5, "quote", 100, 102, 1, 1), sell(1, size=4), close()), "passive", C, cfg
    )
    assert r.summary["maker_fills"] == 0


def test_participation_cap_and_integer_lots():
    r = replay(tape(sell(1, size=7), close()), "passive", C, replace(BASE, participation=0.25))
    assert r.summary["maker_volume_lots"] == 1


def test_sell_aggressor_cannot_fill_our_ask():
    r = replay(tape(sell(1, price=103), close()), "passive", C, BASE)
    assert r.summary["maker_fills"] == 0


def test_no_fill_when_trade_does_not_reach_price():
    r = replay(tape(sell(1, price=101), close()), "passive", C, BASE)
    assert r.summary["maker_fills"] == 0


def test_inventory_limit_survives_replenishment():
    cfg = replace(BASE, max_inventory=4, refresh=0.1, order_size=3)
    r = replay(tape(*[sell(i / 10, size=20) for i in range(1, 20)], close()), "passive", C, cfg)
    assert r.summary["max_abs_inventory"] <= 4
    assert r.summary["terminal_inventory_before_close"] == 4


def test_old_order_remains_live_during_cancel_latency():
    cfg = replace(BASE, refresh=1, latency=0.5)
    events = tape(close(0.6, 101, 103), close(1, 102, 104), sell(1.2, price=100), close(2))
    r = replay(events, "passive", C, cfg)
    assert len(r.fills.query("kind == 'maker'")) == 1
    assert r.fills.iloc[0].price == 100


def test_post_only_rejects_stale_marketable_request():
    cfg = replace(BASE, latency=1)
    r = replay(tape(close(0.5, 96, 98), sell(1.2, price=96), close(2, 96, 98)), "passive", C, cfg)
    assert r.summary["post_only_rejections"] == 1
    assert r.summary["maker_fills"] == 0


def test_no_future_dependence_before_common_horizon():
    prefix = [sell(1), close(2)]
    a = replay(tape(*prefix, close(10, 100, 102)), "as_adaptive", C, BASE)
    b = replay(tape(*prefix, close(10, 10000, 10002)), "as_adaptive", C, BASE)
    np.testing.assert_equal(
        a.trace.query("time < 10").to_numpy(), b.trace.query("time < 10").to_numpy()
    )


def test_self_financing_every_maker_fill():
    r = replay(tape(sell(1), buy(2), close()), "passive", C, replace(BASE, maker_fee_bps=2))
    expected = 0
    for fill in r.fills.itertuples():
        expected -= fill.side * fill.price * fill.size + fill.fee
        assert fill.cash == pytest.approx(expected)


def test_tick_lot_currency_conversion():
    t = tape(sell(1), buy(2), close())
    t.tick_size, t.lot_size = 0.01, 0.001
    r = replay(t, "passive", C, BASE)
    assert r.summary["net_pnl_quote_currency"] == pytest.approx(0.00006)


def test_inventory_skew_moves_both_quotes_down():
    lo = quote_prices(Policy.AS_FIXED, 100, 102, -10, 1, 30, C, BASE)
    hi = quote_prices(Policy.AS_FIXED, 100, 102, 10, 1, 30, C, BASE)
    assert hi[0] <= lo[0] and hi[1] < lo[1]


def test_adaptive_high_volatility_widens_spread():
    low = quote_prices("as_adaptive", 100, 102, 0, 1, 30, C, BASE)
    high = quote_prices("as_adaptive", 100, 102, 0, 100, 30, C, BASE)
    assert high[1] - high[0] > low[1] - low[0]


def test_fixed_policy_does_not_use_adaptive_variance():
    assert quote_prices("as_fixed", 100, 102, 0, 1, 30, C, BASE) == quote_prices(
        "as_fixed", 100, 102, 0, 100, 30, C, BASE
    )


@pytest.mark.parametrize("policy", list(Policy))
def test_all_policy_quotes_passive(policy):
    for inventory in [-100, 0, 100]:
        b, a = quote_prices(policy, 100, 102, inventory, 10, 20, C, BASE)
        assert b < 102 and a > 100 and b < a
