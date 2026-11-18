"""Publication figures built from checked-in result tables, not hard-coded claims."""

import json
from itertools import pairwise
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ORDER = ["passive", "as_fixed", "as_adaptive", "as_imbalance"]
LABELS = {
    "passive": "Passive BBO",
    "as_fixed": "Fixed A–S",
    "as_adaptive": "Adaptive A–S",
    "as_imbalance": "Imbalance A–S",
}
COLORS = {
    "passive": "#64748b",
    "as_fixed": "#2563eb",
    "as_adaptive": "#0f766e",
    "as_imbalance": "#d97706",
}


def theme():
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.labelcolor": "#243247",
            "text.color": "#172033",
            "axes.titleweight": "bold",
            "figure.facecolor": "white",
            "axes.facecolor": "#fbfcfe",
            "grid.alpha": 0.18,
            "savefig.dpi": 180,
            "figure.constrained_layout.use": True,
        }
    )


def save(fig, folder, name):
    fig.savefig(folder / f"{name}.png", bbox_inches="tight")
    fig.savefig(folder / f"{name}.svg", bbox_inches="tight")
    plt.close(fig)


def build_figures(output: Path):
    output = Path(output)
    folder = output / "figures"
    folder.mkdir(exist_ok=True)
    theme()
    core = pd.read_csv(output / "synthetic_trials.csv")
    trace = pd.read_csv(output / "example_trace.csv")
    pairs = pd.read_csv(output / "paired_comparisons.csv")
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    sample = trace[trace.policy == "passive"]
    axes[0].plot(sample.time, sample.mid - sample.mid.iloc[0], color="#334155", lw=1)
    axes[0].set_ylabel("Mid-price change\n(ticks)")
    for policy in ORDER:
        s = trace[trace.policy == policy]
        axes[1].plot(s.time, s.equity, color=COLORS[policy], label=LABELS[policy], lw=1.3)
        axes[2].plot(s.time, s.inventory, color=COLORS[policy], lw=1.1)
    axes[1].set_ylabel("Marked equity\n(tick × lot)")
    axes[2].set_ylabel("Inventory (lots)")
    axes[2].set_xlabel("Seconds into held-out session")
    axes[1].legend(ncol=4, fontsize=8, loc="lower left")
    axes[0].set_title("One shared path, four inventory decisions", loc="left", fontsize=16)
    for ax in axes:
        ax.grid(True)
    fig.suptitle(
        "SYNTHETIC · Illustrative path only; endpoints include liquidation",
        fontsize=10,
        color="#64748b",
    )
    save(fig, folder, "01_shared_path")

    summary = pd.read_csv(output / "synthetic_summary.csv")
    fig, ax = plt.subplots(figsize=(9, 5.5))
    for policy in ORDER:
        s = summary[summary.policy == policy].set_index("metric")
        pnl, risk = s.loc["net_pnl_ticks_lots"], s.loc["rms_inventory"]
        ax.errorbar(
            risk["mean"],
            pnl["mean"],
            xerr=[[risk["mean"] - risk.low], [risk.high - risk["mean"]]],
            yerr=[[pnl["mean"] - pnl.low], [pnl.high - pnl["mean"]]],
            fmt="o",
            color=COLORS[policy],
            ms=9,
            capsize=4,
            label=LABELS[policy],
        )
    ax.axhline(0, color="#64748b", lw=1, ls="--")
    ax.set(
        xlabel="Mean inventory RMS (lots)",
        ylabel="Mean terminal net P&L (tick × lot)",
        title=f"Risk and reward across {core.seed.nunique()} independent paths",
    )
    ax.legend(loc="best")
    ax.grid(True)
    fig.suptitle(
        "SYNTHETIC · Path bootstrap 95% intervals · Fees and terminal liquidation included",
        fontsize=10,
        color="#64748b",
    )
    save(fig, folder, "02_risk_reward")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.8))
    for ax, metric, title in zip(
        axes,
        ["net_pnl_ticks_lots", "rms_inventory"],
        ["Net P&L difference", "Inventory RMS difference"],
    ):
        rows = pairs[pairs.metric == metric].set_index("policy")
        for j, policy in enumerate(ORDER[1:]):
            s = rows.loc[policy]
            ax.errorbar(
                s.mean_difference,
                j,
                xerr=[[s.mean_difference - s.ci_low], [s.ci_high - s.mean_difference]],
                fmt="o",
                color=COLORS[policy],
                ms=8,
                capsize=5,
            )
        ax.axvline(0, color="#64748b", ls="--", lw=1)
        ax.set_yticks(range(3), [LABELS[p] for p in ORDER[1:]])
        ax.set_xlabel("Policy minus passive BBO")
        ax.set_title(title)
        ax.grid(axis="x")
    fig.suptitle(
        "SYNTHETIC · Paired comparisons on identical test tapes (95% bootstrap intervals)",
        fontsize=11,
    )
    save(fig, folder, "03_paired_effects")

    ranks = pd.read_csv(output / "rank_stability.csv")
    fig, axes = plt.subplots(2, 3, figsize=(13, 7.5), sharex=True, sharey=True)
    for row, queue in enumerate(["front", "back"]):
        for col, fee in enumerate([0.0, 1.0, 2.0]):
            ax = axes[row, col]
            part = ranks[(ranks.queue == queue) & (ranks.maker_fee_bps == fee)]
            matrix = part.pivot(
                index=["adverse_selection", "latency"], columns="policy", values="rank"
            )[ORDER]
            im = ax.imshow(matrix.to_numpy(), vmin=1, vmax=4, cmap="viridis_r", aspect="auto")
            for i in range(matrix.shape[0]):
                for j in range(4):
                    value = matrix.iloc[i, j]
                    ax.text(
                        j,
                        i,
                        f"{value:g}",
                        ha="center",
                        va="center",
                        color="white" if value > 2.5 else "#152238",
                        fontsize=10,
                    )
            ax.set_title(f"{queue.title()} queue · {fee:g} bps maker fee", fontsize=11)
            ax.set_xticks(range(4), ["BBO", "Fixed", "Adaptive", "Imbalance"])
            ax.set_yticks(
                range(len(matrix)), [f"a={a:g} · {int(l * 1000)} ms" for a, l in matrix.index]
            )
    fig.colorbar(
        im,
        ax=axes,
        label="Rank of mean terminal net P&L (1 = highest)",
        shrink=0.8,
        ticks=[1, 2, 3, 4],
    )
    fig.suptitle("Policy rankings across execution assumptions", fontsize=17, fontweight="bold")
    fig.supxlabel(
        "SYNTHETIC · a = adverse-selection coefficient · Equal weights per scenario; rank ignores effect magnitude",
        fontsize=10,
        color="#64748b",
    )
    save(fig, folder, "04_rank_stability")

    calibration = json.loads((output / "calibration_example.json").read_text())
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    d = np.array(calibration["depths"])
    axes[0].plot(d, calibration["counts"], "o", color="#0f766e", label="Observed tail counts")
    axes[0].plot(d, calibration["predicted_counts"], color="#2563eb", label="Poisson composite fit")
    axes[0].set(
        xlabel="Distance from pre-trade mid (ticks)",
        ylabel="Count in training window",
        title="Calibration retains zero-count bins",
    )
    axes[0].legend(fontsize=8)
    axes[0].grid(True)
    cal = pd.read_csv(output / "calibration_trials.csv")
    axes[1].hist(cal.kappa, bins=25, color="#0f766e", alpha=0.8)
    axes[1].set(
        xlabel="Fitted depth-decay κ (1/tick)",
        ylabel="Independent paths",
        title="Parameter variation across training samples",
    )
    fig.suptitle(
        "SYNTHETIC · Training data only · Nested counts imply composite, not independent, likelihood",
        fontsize=10,
        color="#64748b",
    )
    save(fig, folder, "05_calibration")

    # Economic-preference map: an explicit objective, not P&L-driven retuning.
    profit = core.groupby("policy").net_pnl_ticks_lots.mean()
    risk = core.assign(risk=core.rms_inventory**2).groupby("policy").risk.mean()
    penalty = np.linspace(0, 150, 301)
    utility = np.array([profit[p] - penalty * risk[p] for p in ORDER])
    winners = np.argmax(utility, axis=0)
    fig, axes = plt.subplots(
        2, 1, figsize=(11, 5), gridspec_kw={"height_ratios": [1, 3]}, sharex=True
    )
    from matplotlib.colors import ListedColormap

    axes[0].imshow(
        winners[np.newaxis, :],
        aspect="auto",
        extent=[0, 150, 0, 1],
        cmap=ListedColormap([COLORS[p] for p in ORDER]),
        vmin=0,
        vmax=3,
    )
    boundaries = np.r_[0, np.flatnonzero(np.diff(winners)) + 1, len(winners)]
    short_names = {0: "BBO", 1: "Fixed A–S", 2: "Adaptive A–S", 3: "Imbalance A–S"}
    for left, right in pairwise(boundaries):
        center = (penalty[left] + penalty[right - 1]) / 2
        axes[0].text(
            center,
            0.5,
            short_names[int(winners[left])],
            ha="center",
            va="center",
            color="white",
            fontsize=8 if right - left < 20 else 10,
            fontweight="bold",
        )
    axes[0].set_yticks([])
    axes[0].set_ylabel("Highest mean\nutility", fontsize=9)
    for policy in ORDER:
        if policy != "passive":
            axes[1].plot(
                penalty,
                (profit[policy] - profit["passive"]) - penalty * (risk[policy] - risk["passive"]),
                color=COLORS[policy],
                label=LABELS[policy],
                lw=2,
            )
    axes[1].axhline(0, color="#64748b", lw=1, ls="--")
    axes[1].set(
        xlabel="Inventory penalty λ (tick–lots per squared lot)",
        ylabel="Mean utility difference vs BBO",
    )
    axes[1].legend(ncol=3, fontsize=9)
    axes[1].grid(True)
    fig.suptitle("The objective determines the preferred policy", fontsize=15, fontweight="bold")
    fig.supxlabel(
        "SYNTHETIC · Exploratory preference: mean net P&L − λ × mean squared inventory RMS",
        fontsize=10,
        color="#64748b",
    )
    save(fig, folder, "07_risk_preference")

    if (output / "historical_blocks.csv").exists():
        real = pd.read_csv(output / "historical_blocks.csv")
        if "symbol" not in real:
            return sorted(p.name for p in folder.glob("*.png"))
        symbols = sorted(real.symbol.unique())
        fig, axes = plt.subplots(len(symbols), 2, figsize=(12, 4.2 * len(symbols)), squeeze=False)
        for row, symbol in enumerate(symbols):
            for col, queue in enumerate(["front", "back"]):
                ax = axes[row, col]
                part = real[
                    (real.symbol == symbol) & (real.queue == queue) & (real.latency == 0.05)
                ]
                for policy in ORDER:
                    values = (
                        part[part.policy == policy]
                        .groupby("maker_fee_bps")
                        .agg(
                            net=("net_pnl_quote_currency", "sum"),
                            turnover=("maker_notional_quote_currency", "sum"),
                        )
                    )
                    bps = values.net / values.turnover.replace(0, np.nan) * 10000
                    ax.plot(values.index, bps, "o-", label=LABELS[policy], color=COLORS[policy])
                ax.axhline(0, color="#64748b", lw=1, ls="--")
                ax.set(
                    title=f"{symbol} · {queue} queue",
                    xlabel="Assumed maker fee (bps)",
                    ylabel="Net / maker turnover (bps)",
                )
                ax.grid(True)
        axes[0, 1].legend(fontsize=8)
        fig.suptitle("HISTORICAL · Costs and queue assumptions", fontsize=16, fontweight="bold")
        fig.supxlabel(
            "All selected dates pooled within asset · 50 ms order latency · 4 bps terminal taker fee · Descriptive only",
            fontsize=9,
            color="#64748b",
        )
        save(fig, folder, "06_historical_fee_sensitivity")
        if (output / "historical_break_even.csv").exists():
            be = pd.read_csv(output / "historical_break_even.csv")
            be = be[(be.queue == "back") & (be.latency == 0.05)]
            fig, axes = plt.subplots(1, len(symbols), figsize=(12, 5), squeeze=False)
            for j, symbol in enumerate(symbols):
                ax = axes[0, j]
                part = be[be.symbol == symbol]
                days = sorted(part.day.unique())
                for k, policy in enumerate(ORDER):
                    vals = (
                        part[part.policy == policy]
                        .set_index("day")
                        .reindex(days)
                        .break_even_maker_fee_bps
                    )
                    ax.bar(
                        np.arange(len(days)) + (k - 1.5) * 0.19,
                        vals,
                        width=0.18,
                        color=COLORS[policy],
                        label=LABELS[policy],
                    )
                ax.axhline(0, color="#334155", lw=1)
                ax.axhline(
                    1,
                    color="#64748b",
                    ls="--",
                    lw=1,
                    label="1 bp reference fee" if j == 1 else None,
                )
                ax.set_xticks(range(len(days)), [d[5:] for d in days])
                ax.set(title=symbol, xlabel="2024 date (UTC)", ylabel="Break-even maker fee (bps)")
                ax.grid(axis="y")
            axes[0, -1].legend(fontsize=8, loc="best")
            fig.suptitle(
                "HISTORICAL · A negative break-even fee requires a rebate",
                fontsize=14,
                fontweight="bold",
            )
            fig.supxlabel(
                "Back-queue proxy · 50 ms latency · Closing costs retained · Fill path held fixed as fees change",
                fontsize=10,
                color="#64748b",
            )
            save(fig, folder, "08_historical_break_even")
    return sorted(p.name for p in folder.glob("*.png"))
