"""Executable experiments: artifacts contain actual estimates and provenance."""

import hashlib
import json
import platform
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import scipy

from .calibration import calibrate
from .data import load_binance_streaming
from .engine import replay
from .policies import Policy
from .statistics import mean_interval, paired_comparisons, rank_stability
from .synthetic import SyntheticConfig, generate
from .types import ReplayConfig

POLICIES = list(Policy)


def source_digest():
    h = hashlib.sha256()
    root = Path(__file__).parent
    for path in sorted(root.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


SOURCE_HASH_AT_IMPORT = source_digest()


def manifest(name, started, config):
    if source_digest() != SOURCE_HASH_AT_IMPORT:
        raise RuntimeError(
            "Source changed during the experiment; rerun before publishing artifacts"
        )
    return {
        "experiment": name,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "wall_seconds": time.perf_counter() - started,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "pandas": pd.__version__,
        "source_sha256": SOURCE_HASH_AT_IMPORT,
        "configuration": config,
    }


def run_synthetic(output: Path, quick=False, trials=None, stress_trials=None):
    start = time.perf_counter()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    n = trials if trials is not None else (12 if quick else 256)
    ns = stress_trials if stress_trials is not None else (3 if quick else 32)
    duration = 180.0 if quick else 600.0
    training_end = duration / 4
    config = ReplayConfig()
    generation = SyntheticConfig(duration=duration)
    rows, calibrations, traces, fills = [], [], [], []
    first_fit = None
    for i in range(n):
        seed = 12000 + i
        tape = generate(seed, generation)
        train, test = tape.window(0, training_end), tape.window(training_end, duration)
        fit, diagnostic = calibrate(train, config)
        calibrations.append(
            {"seed": seed, **asdict(fit), "optimizer_success": diagnostic["success"]}
        )
        if first_fit is None:
            first_fit = diagnostic
        for policy in POLICIES:
            result = replay(test, policy, fit, config, record=i == 0)
            rows.append({"seed": seed, **result.summary})
            if i == 0:
                traces.append(result.trace.assign(policy=policy.value))
                fills.append(result.fills.assign(policy=policy.value))
        if (i + 1) % 32 == 0:
            print(f"Synthetic core: {i + 1}/{n} paths", flush=True)
    core = pd.DataFrame(rows)
    core.to_csv(output / "synthetic_trials.csv", index=False)
    pd.DataFrame(calibrations).to_csv(output / "calibration_trials.csv", index=False)
    pd.concat(traces).to_csv(output / "example_trace.csv", index=False)
    pd.concat(fills).to_csv(output / "example_fills.csv", index=False)
    (output / "calibration_example.json").write_text(json.dumps(first_fit, indent=2))
    comparisons = pd.concat(
        [paired_comparisons(core, metric) for metric in ("net_pnl_ticks_lots", "rms_inventory")],
        ignore_index=True,
    )
    comparisons.to_csv(output / "paired_comparisons.csv", index=False)
    summaries = []
    for j, (policy, block) in enumerate(core.groupby("policy")):
        for metric in (
            "net_pnl_ticks_lots",
            "rms_inventory",
            "max_drawdown_ticks_lots",
            "maker_fills",
        ):
            summaries.append(
                {"policy": policy, "metric": metric, **mean_interval(block[metric], seed=10 + j)}
            )
    pd.DataFrame(summaries).to_csv(output / "synthetic_summary.csv", index=False)
    stress_rows = []
    for adverse in (0.0, 0.5):
        for i in range(ns):
            seed = 42000 + i
            tape = generate(seed, replace(generation, adverse_selection=adverse))
            train, test = tape.window(0, training_end), tape.window(training_end, duration)
            fit, _ = calibrate(train, config)
            for queue in ("front", "back"):
                for latency in (0.0, 0.05, 0.25):
                    for fee in (0.0, 1.0, 2.0):
                        cfg = replace(config, queue=queue, latency=latency, maker_fee_bps=fee)
                        factors = {
                            "seed": seed,
                            "queue": queue,
                            "latency": latency,
                            "maker_fee_bps": fee,
                            "adverse_selection": adverse,
                        }
                        for policy in POLICIES:
                            result = replay(test, policy, fit, cfg, record=False)
                            stress_rows.append(factors | result.summary)
            if (i + 1) % 8 == 0:
                print(f"Stress adverse={adverse}: {i + 1}/{ns} paths", flush=True)
    stress = pd.DataFrame(stress_rows)
    stress.to_csv(output / "stress_trials.csv", index=False)
    ranks = rank_stability(stress)
    ranks.to_csv(output / "rank_stability.csv", index=False)
    stress_paired = []
    factors = ["queue", "latency", "maker_fee_bps", "adverse_selection"]
    for values, group in stress.groupby(factors):
        comparison = paired_comparisons(group)
        for key, value in zip(factors, values):
            comparison[key] = value
        stress_paired.append(comparison)
    pd.concat(stress_paired).to_csv(output / "stress_paired_comparisons.csv", index=False)
    config_record = {
        "mode": "quick" if quick else "full",
        "independent_core_paths": n,
        "stress_paths_per_cell": ns,
        "seed_core_start": 12000,
        "seed_stress_start": 42000,
        "training_end_s": training_end,
        "generator": asdict(generation),
        "replay": asdict(config),
        "policy_evaluations": len(core) + len(stress),
        "bootstrap_repetitions": 3000,
    }
    metadata = manifest("synthetic core and factorial stress", start, config_record)
    (output / "manifest.json").write_text(json.dumps(metadata, indent=2))
    return metadata


def run_historical(
    output: Path,
    cache: Path,
    days=("2024-01-01", "2024-01-02", "2024-01-03"),
    seconds=7200,
    symbols=("ETHUSDT", "BTCUSDT"),
):
    start = time.perf_counter()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows, sources, calibrations = [], [], []
    trace_frames = []
    instrument_specs = {
        "ETHUSDT": {"tick_size": 0.01, "lot_size": 0.001, "order_size": 10, "max_inventory": 100},
        "BTCUSDT": {"tick_size": 0.1, "lot_size": 0.001, "order_size": 1, "max_inventory": 10},
    }
    for symbol in symbols:
        if symbol not in instrument_specs:
            raise ValueError("historical experiment supports ETHUSDT and BTCUSDT")
        spec = instrument_specs[symbol]
        config = ReplayConfig(
            order_size=spec["order_size"],
            max_inventory=spec["max_inventory"],
            latency=0.05,
            refresh=1,
            queue="back",
            maker_fee_bps=1,
            taker_fee_bps=4,
        )
        for day in days:
            print(f"Loading recorded {symbol} {day}", flush=True)
            window_origin = 39600.0 if day == "2024-01-03" else 0.0
            tape, provenance = load_binance_streaming(
                symbol,
                day,
                Path(cache),
                seconds,
                tick_size=spec["tick_size"],
                lot_size=spec["lot_size"],
                start_seconds=window_origin,
            )
            sources.append(provenance)
            training_end = 1800.0
            fit, diagnostic = calibrate(tape.window(0, training_end), config)
            calibrations.append(
                {"symbol": symbol, "day": day, "fit": asdict(fit), "diagnostics": diagnostic}
            )
            for block_id, block_start in enumerate(np.arange(training_end, seconds - 899, 900)):
                test = tape.window(float(block_start), float(block_start + 900))
                for queue in ("front", "back"):
                    for latency in (0.05, 0.25):
                        for fee in (0.0, 1.0, 2.0):
                            cfg = replace(config, queue=queue, latency=latency, maker_fee_bps=fee)
                            for policy in POLICIES:
                                record = (
                                    block_id == 0
                                    and queue == "back"
                                    and latency == 0.05
                                    and fee == 1
                                )
                                result = replay(test, policy, fit, cfg, record=record)
                                rows.append(
                                    {
                                        "symbol": symbol,
                                        "day": day,
                                        "block": block_id,
                                        "block_start_utc_seconds": window_origin + block_start,
                                        "queue": queue,
                                        "latency": latency,
                                        "maker_fee_bps": fee,
                                        **result.summary,
                                    }
                                )
                                if record:
                                    trace_frames.append(
                                        result.trace.assign(
                                            policy=policy.value, symbol=symbol, day=day
                                        )
                                    )
                print(f"Historical {symbol} {day}: evaluated block {block_id + 1}", flush=True)
            # The verified compact window is sufficient for exact reruns. Delete
            # only this downloader's raw archives, if an earlier run stored them.
            for kind in ("bookTicker", "aggTrades"):
                raw = Path(cache) / f"{symbol}-{kind}-{day}.zip"
                if raw.exists():
                    raw.unlink()
    frame = pd.DataFrame(rows)
    frame.to_csv(output / "historical_blocks.csv", index=False)
    pd.concat(trace_frames).to_csv(output / "historical_example_trace.csv", index=False)
    (output / "historical_calibration.json").write_text(json.dumps(calibrations, indent=2))
    (output / "data_provenance.json").write_text(json.dumps(sources, indent=2))
    factors = ["symbol", "day", "queue", "latency", "policy"]
    zero = (
        frame[frame.maker_fee_bps == 0]
        .groupby(factors)
        .agg(
            net_pnl_quote_currency=("net_pnl_quote_currency", "sum"),
            maker_notional_quote_currency=("maker_notional_quote_currency", "sum"),
            maker_fills=("maker_fills", "sum"),
        )
        .reset_index()
    )
    zero["break_even_maker_fee_bps"] = (
        zero.net_pnl_quote_currency / zero.maker_notional_quote_currency.replace(0, np.nan) * 10000
    )
    zero.to_csv(output / "historical_break_even.csv", index=False)
    metadata = manifest(
        "historical descriptive replay",
        start,
        {
            "days": list(days),
            "symbols": list(symbols),
            "seconds_per_day": seconds,
            "training_end_s": 1800,
            "test_block_s": 900,
            "window_origin_utc_seconds": {d: 39600 if d == "2024-01-03" else 0 for d in days},
            "instrument_specs": instrument_specs,
            "replay_common": asdict(config),
            "policy_evaluations": len(frame),
            "confidence_intervals": "none: dependent blocks; only three dates",
        },
    )
    (output / "historical_manifest.json").write_text(json.dumps(metadata, indent=2))
    return metadata
