"""Independently reconcile committed tables; run after reproducing both studies."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from microstructure_mm.experiments import source_digest


def verify():
    results = ROOT / "results"
    expected_source = source_digest()
    synthetic_manifest = json.loads((results / "manifest.json").read_text())
    historical_manifest = json.loads((results / "historical_manifest.json").read_text())
    for manifest in (synthetic_manifest, historical_manifest):
        assert manifest["source_sha256"] == expected_source, "Source changed: rerun the experiments"
    core = pd.read_csv(results / "synthetic_trials.csv")
    stress = pd.read_csv(results / "stress_trials.csv")
    real = pd.read_csv(results / "historical_blocks.csv")
    assert len(core) + len(stress) == synthetic_manifest["configuration"]["policy_evaluations"]
    assert len(real) == historical_manifest["configuration"]["policy_evaluations"]
    residual_max = 0.0
    for frame in (core, stress, real):
        residual = (
            (
                frame.net_pnl_ticks_lots
                - frame.spread_capture_ticks_lots
                - frame.inventory_revaluation_ticks_lots
                + frame.fees_ticks_lots
                + frame.liquidation_spread_cost
            )
            .abs()
            .max()
        )
        residual_max = max(residual_max, float(residual))
        assert residual < 1e-6, f"Cash identity failed by {residual}"
        assert np.isfinite(frame.net_pnl_ticks_lots).all()
        assert (frame.rms_inventory >= 0).all()
    assert core.max_abs_inventory.max() <= 10
    assert stress.max_abs_inventory.max() <= 10
    caps = real.symbol.map({"ETHUSDT": 100, "BTCUSDT": 10})
    assert (real.max_abs_inventory <= caps).all()
    paired = pd.read_csv(results / "paired_comparisons.csv")
    for row in paired.itertuples():
        pivot = core.pivot(index="seed", columns="policy", values=row.metric)
        actual = (pivot[row.policy] - pivot[row.baseline]).mean()
        assert abs(actual - row.mean_difference) < 1e-9
    keys = ["symbol", "day", "block", "queue", "latency", "policy"]
    zero = real[real.maker_fee_bps == 0].set_index(keys)
    for fee in (1.0, 2.0):
        other = real[real.maker_fee_bps == fee].set_index(keys).reindex(zero.index)
        expected = zero.net_pnl_quote_currency - fee / 10000 * zero.maker_notional_quote_currency
        np.testing.assert_allclose(other.net_pnl_quote_currency, expected, atol=1e-7, rtol=1e-9)
        np.testing.assert_array_equal(other.maker_fills, zero.maker_fills)
    threshold = pd.read_csv(results / "historical_break_even.csv")
    expected = (
        threshold.net_pnl_quote_currency
        / threshold.maker_notional_quote_currency.replace(0, np.nan)
        * 10000
    )
    np.testing.assert_allclose(expected, threshold.break_even_maker_fee_bps, equal_nan=True)
    provenance = json.loads((results / "data_provenance.json").read_text())
    assert len(provenance) == 6
    assert all(len(source["sha256"]) == 64 for p in provenance for source in p["sources"])
    record = {
        "source_sha256": expected_source,
        "synthetic_rows": len(core),
        "stress_rows": len(stress),
        "historical_rows": len(real),
        "recorded_events": sum(p["events"] for p in provenance),
        "max_accounting_residual_tick_lots": residual_max,
        "checks": "source freshness, row counts, cash identity, risk caps, paired means, fee linearity, break-even, provenance",
    }
    (results / "verification.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    verify()
