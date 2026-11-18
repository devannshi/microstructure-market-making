"""Uncertainty at the independent simulation-path unit, never at the fill unit."""

import numpy as np
import pandas as pd
from scipy.stats import rankdata


def mean_interval(values, seed=2026, repetitions=3000, confidence=0.95):
    x = np.asarray(values, float)
    x = x[np.isfinite(x)]
    if len(x) < 2:
        return {
            "mean": float(np.mean(x)) if len(x) else np.nan,
            "low": np.nan,
            "high": np.nan,
            "n": len(x),
        }
    rng = np.random.default_rng(seed)
    means = x[rng.integers(0, len(x), size=(repetitions, len(x)))].mean(axis=1)
    alpha = (1 - confidence) / 2
    return {
        "mean": float(x.mean()),
        "low": float(np.quantile(means, alpha)),
        "high": float(np.quantile(means, 1 - alpha)),
        "n": len(x),
    }


def paired_comparisons(frame: pd.DataFrame, metric="net_pnl_ticks_lots", baseline="passive"):
    table = frame.pivot(index="seed", columns="policy", values=metric)
    if baseline not in table:
        raise ValueError("baseline missing")
    rows = []
    for i, policy in enumerate(table.columns):
        if policy == baseline:
            continue
        paired = table[[baseline, policy]].dropna()
        delta = paired[policy].to_numpy() - paired[baseline].to_numpy()
        stat = mean_interval(delta, seed=2026 + i)
        rows.append(
            {
                "policy": policy,
                "baseline": baseline,
                "metric": metric,
                "mean_difference": stat["mean"],
                "ci_low": stat["low"],
                "ci_high": stat["high"],
                "independent_paths": stat["n"],
                "win_fraction": float(np.mean(delta > 0)),
                "tie_fraction": float(np.mean(delta == 0)),
            }
        )
    return pd.DataFrame(rows)


def rank_stability(frame: pd.DataFrame):
    factors = ["queue", "latency", "maker_fee_bps", "adverse_selection"]
    rows = []
    for values, block in frame.groupby(factors, sort=True):
        means = block.groupby("policy")["net_pnl_ticks_lots"].mean()
        ranks = rankdata(-means.to_numpy(), method="average")
        for policy, score, rank in zip(means.index, means, ranks):
            rows.append(
                dict(zip(factors, values))
                | {
                    "policy": policy,
                    "mean_pnl": score,
                    "rank": rank,
                    "n_paths": block.seed.nunique(),
                }
            )
    return pd.DataFrame(rows)
