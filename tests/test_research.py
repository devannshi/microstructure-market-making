import numpy as np
import pandas as pd
import pytest

from microstructure_mm import Event, ReplayConfig, Tape
from microstructure_mm.calibration import calibrate, poisson_depth_fit
from microstructure_mm.data import read_tape, write_tape
from microstructure_mm.statistics import mean_interval, paired_comparisons
from microstructure_mm.synthetic import SyntheticConfig, generate


@pytest.mark.parametrize(
    "kwargs",
    [
        {"latency": -1},
        {"queue": "magic"},
        {"participation": 0},
        {"participation": 2},
        {"order_size": 20},
        {"refresh": 0},
    ],
)
def test_invalid_config_rejected(kwargs):
    with pytest.raises(ValueError):
        ReplayConfig(**kwargs)


@pytest.mark.parametrize("args", [(0, "quote", 102, 100), (0, "quote", 0, 1), (0, "oops")])
def test_invalid_events_rejected(args):
    with pytest.raises(ValueError):
        Event(*args)


def test_unsorted_events_rejected():
    with pytest.raises(ValueError):
        Tape([Event(2, "quote", 100, 102), Event(1, "quote", 100, 102)])


def test_generated_events_are_reproducible():
    cfg = SyntheticConfig(duration=5)
    assert generate(19, cfg).events == generate(19, cfg).events
    assert generate(19, cfg).events != generate(20, cfg).events


def test_generated_tape_has_no_invalid_spreads():
    t = generate(16, SyntheticConfig(duration=30))
    assert all(e.ask > e.bid > 0 for e in t.events if e.kind == "quote")
    assert all(a.time <= b.time for a, b in zip(t.events, t.events[1:]))


def test_window_uses_only_previous_quote():
    t = Tape(
        [
            Event(0, "quote", 100, 102),
            Event(2, "trade", price=100, size=1, side=-1),
            Event(3, "quote", 200, 202),
        ]
    )
    w = t.window(1, 3)
    assert w.events[0].time == 0 and w.events[0].bid == 100
    assert all(e.bid != 200 for e in w.events)


def test_poisson_fit_recovers_known_intensity():
    depths = np.arange(0, 8, dtype=float)
    counts = 1000 * 3 * np.exp(-0.7 * depths)
    rate, kappa, diag = poisson_depth_fit(depths, counts, 1000)
    assert rate == pytest.approx(3, rel=0.001)
    assert kappa == pytest.approx(0.7, rel=0.001)
    assert diag["success"]


def test_zero_bins_are_supported_without_log_zero():
    rate, kappa, _diag = poisson_depth_fit(np.arange(4), np.array([30, 5, 0, 0]), 10)
    assert np.isfinite(rate) and np.isfinite(kappa) and kappa > 0


def test_all_zero_counts_produce_explicit_fallback():
    _, _, diag = poisson_depth_fit(np.arange(4), np.zeros(4), 10)
    assert not diag["success"] and diag["reason"] == "no_tail_trades"


def test_calibration_does_not_mutate_data():
    t = generate(2, SyntheticConfig(duration=30))
    original = list(t.events)
    fit, _ = calibrate(t)
    assert fit.end == t.events[-1].time and t.events == original
    assert fit.variance_rate > 0 and fit.kappa > 0


def test_bootstrap_reproducibility_and_constant_effect():
    a = mean_interval(np.repeat(2.0, 100))
    assert a["mean"] == a["low"] == a["high"] == 2
    assert a == mean_interval(np.repeat(2.0, 100))


def test_paired_differences_cancel_common_path_noise():
    frame = pd.DataFrame(
        [
            {"seed": s, "policy": p, "net_pnl_ticks_lots": s * 100 + effect}
            for s in range(20)
            for p, effect in [("passive", 0), ("as_fixed", 3)]
        ]
    )
    out = paired_comparisons(frame).iloc[0]
    assert out.mean_difference == out.ci_low == out.ci_high == 3


def test_csv_roundtrip(tmp_path):
    t = generate(4, SyntheticConfig(duration=2))
    path = tmp_path / "test.csv"
    write_tape(t, path)
    recovered = read_tape(path)
    assert t.events == recovered.events
    assert recovered.tick_size == t.tick_size and recovered.lot_size == t.lot_size
    assert recovered.session_end == t.session_end and recovered.source == t.source
