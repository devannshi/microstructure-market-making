"""Generate readable conclusions from saved, auditable experiment tables."""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from .plots import LABELS, ORDER


def table(headers, rows):
    return "\n".join(
        ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
        + ["| " + " | ".join(str(v) for v in row) + " |" for row in rows]
    )


def build_report(output: Path):
    output = Path(output)
    manifest = json.loads((output / "manifest.json").read_text())
    summary = pd.read_csv(output / "synthetic_summary.csv")
    pairs = pd.read_csv(output / "paired_comparisons.csv")
    core = pd.read_csv(output / "synthetic_trials.csv")
    ranks = pd.read_csv(output / "rank_stability.csv")
    n = core.seed.nunique()
    rows = []
    for policy in ORDER:
        s = summary[summary.policy == policy].set_index("metric")
        pnl = s.loc["net_pnl_ticks_lots"]
        rows.append(
            [
                LABELS[policy],
                f"{pnl['mean']:.2f}",
                f"[{pnl.low:.2f}, {pnl.high:.2f}]",
                f"{s.loc['rms_inventory', 'mean']:.3f}",
                f"{s.loc['max_drawdown_ticks_lots', 'mean']:.2f}",
            ]
        )
    baseline_rms = core[core.policy == "passive"].rms_inventory.mean()
    adaptive_rms = core[core.policy == "as_adaptive"].rms_inventory.mean()
    effect = pairs[(pairs.policy == "as_adaptive") & (pairs.metric == "net_pnl_ticks_lots")].iloc[0]
    winner_counts = ranks[ranks["rank"] == 1].policy.value_counts()
    winners = ", ".join(f"{LABELS[p]}: {v}" for p, v in winner_counts.items())
    cells = ranks.groupby(["queue", "latency", "maker_fee_bps", "adverse_selection"]).ngroups
    threshold_rows = []
    mean_profit = core.groupby("policy").net_pnl_ticks_lots.mean()
    mean_risk = (
        core.assign(inventory_squared=core.rms_inventory**2)
        .groupby("policy")
        .inventory_squared.mean()
    )
    for policy in ORDER[1:]:
        denominator = mean_risk["passive"] - mean_risk[policy]
        threshold = (
            (mean_profit["passive"] - mean_profit[policy]) / denominator
            if denominator > 0
            else np.nan
        )
        threshold_rows.append([LABELS[policy], f"{threshold:.3f}"])
    text = rf"""# Research results

These tables were generated from the committed CSV files. They distinguish independent simulated experiments from a small descriptive historical case study. See the [protocol](../docs/EXPERIMENT_PROTOCOL.md), [methodology](../docs/METHODOLOGY.md), and [run manifest](manifest.json).

## Main finding

Inventory control has a measurable price. Across **{n} independent held-out synthetic paths**, adaptive A–S reduced average inventory RMS by **{100 * (1 - adaptive_rms / baseline_rms):.1f}%** relative to passive BBO, while changing mean net P&L by **{effect.mean_difference:.2f} tick–lots** (paired 95% interval **[{effect.ci_low:.2f}, {effect.ci_high:.2f}]**). The extension is therefore not described as a raw-profit improvement. The synthetic imbalance feature is lagged and nonpredictive of subsequent flow; its failure is a negative-control-style result, not evidence against trained predictive microprice signals.

{table(["Policy", "Mean net P&L", "95% mean interval", "Mean inventory RMS", "Mean max drawdown"], rows)}

P&L and drawdown are tick × lot; inventory is lots. The primary reference scenario has a back-queue proxy, 50 ms order latency, zero maker fee and 1 bp terminal taker fee. Every endpoint includes closing spread and liquidation fee. Intervals resample independent paths, not fills. This generator is not calibrated to a particular exchange.

![Risk and reward](figures/02_risk_reward.png)

## Paired evidence

{table(["Policy", "P&L difference vs BBO", "Paired 95% interval", "Fraction of paths with higher P&L"], [[LABELS[r.policy], f"{r.mean_difference:.2f}", f"[{r.ci_low:.2f}, {r.ci_high:.2f}]", f"{100 * r.win_fraction:.1f}%"] for r in pairs[pairs.metric == "net_pnl_ticks_lots"].itertuples()])}

![Paired effects](figures/03_paired_effects.png)

## Execution-assumption surface

The factorial grid contains **{cells} scenarios**, each with **{int(ranks.n_paths.iloc[0])} independent paths** paired across four policies. The highest mean-P&L counts are **{winners}**. A cell rank alone says nothing about significance or effect size; the raw trials and paired intervals are also provided. In this experiment the leading raw-profit policy is stable, while fixed/adaptive relative rankings can change with costs.

![Ranking surface](figures/04_rank_stability.png)

The grid varies front/back queue placement, 0/50/250 ms order latency, 0/1/2 bps maker cost, and 0/0.5 tick signed-flow adverse selection. A positive synthetic mean in a low-cost cell is not an estimate of achievable market returns. The complete grid is in [stress_trials.csv](stress_trials.csv), with paired effects in [stress_paired_comparisons.csv](stress_paired_comparisons.csv).

## Exploratory economic preference calculation

Define a preference score $J_\lambda=E[P]-\lambda E[R]$, where $P$ is terminal net P&L and $R=q_{{\mathrm{{RMS}}}}^2$ is squared inventory RMS on each path. The risk-penalty level where a candidate matches passive BBO in the estimated means is $(\bar P_{{BBO}}-\bar P_j)/(\bar R_{{BBO}}-\bar R_j)$.

{table(["Policy", "Break-even inventory penalty λ"], threshold_rows)}

The units of λ are tick–lots per squared lot. These are descriptive preference thresholds on the same simulated sample, not a new policy selected and independently validated afterward. The calculation makes the choice of economic objective explicit.

![Risk-preference map](figures/07_risk_preference.png)

## Calibration and reproducibility

![Calibration](figures/05_calibration.png)

Training estimates, optimizer diagnostics, and zero-count tail bins are retained. The exposure-adjusted count objective is a composite likelihood because thresholds overlap. No naive Hessian-based inference is claimed. Core source digest: `{manifest["source_sha256"]}`. The saved synthetic run contains **{manifest["configuration"]["policy_evaluations"]:,} policy evaluations** and took **{manifest["wall_seconds"]:.1f} seconds** on the environment recorded in the manifest; runtime is machine dependent.
"""
    if (output / "historical_blocks.csv").exists():
        frame = pd.read_csv(output / "historical_blocks.csv")
        sources = json.loads((output / "data_provenance.json").read_text())
        if "symbol" in frame:
            base = frame[
                (frame.queue == "back") & (frame.latency == 0.05) & (frame.maker_fee_bps == 1)
            ]
            groups = base.groupby(["symbol", "day", "policy"]).agg(
                pnl=("net_pnl_quote_currency", "sum"),
                notional=("maker_notional_quote_currency", "sum"),
                fills=("maker_fills", "sum"),
                rms=("rms_inventory", "mean"),
            )
            hist_rows = []
            for (symbol, day, policy), r in groups.iterrows():
                bps = 10000 * r.pnl / r.notional if r.notional else np.nan
                hist_rows.append(
                    [symbol, day, LABELS[policy], f"{r.pnl:.4f}", f"{bps:.2f}", int(r.fills)]
                )
            positive = int((groups.pnl > 0).sum())
            events = sum(s["events"] for s in sources)
            text += rf"""
## Historical case study

**{events:,} recorded events** across **{len(sources)} asset-date windows**, evaluated as **{len(frame):,} policy × block × execution-scenario runs**. The reference execution scenario below assumes back queue, 50 ms order latency, 1 bp maker cost and 4 bps terminal taker cost. **{positive} of {len(groups)} asset-date-policy totals are positive** under those assumptions. This is a report of the tested sample, not a claim of a persistent trading edge.

{table(["Asset", "Date", "Policy", "Net P&L (USDT)", "Net / maker turnover (bps)", "Maker fill events"], hist_rows)}

Order sizes are 0.01 ETH or 0.001 BTC; position limits are 0.1 ETH or 0.01 BTC. All strategies use identical sizes within an asset. Each 15-minute block starts and ends flat. The six contiguous blocks per asset-date are dependent. No IID confidence interval is assigned to these empirical totals.

![Historical cost sensitivity](figures/06_historical_fee_sensitivity.png)

### Conditional maker-fee break-even

The exact conditional threshold is $f^*=10^4 P_0/N$, where $P_0$ is net P&L at zero maker fee (still including closing costs) and $N$ is maker turnover. Changing the fee does not change quotes or fills in this model, so $P(f)=P_0-fN/10^4$. A negative threshold means a rebate would be required to break even even before considering additional omitted costs. This is not an estimate of an actual exchange's fee tier.

[historical_break_even.csv](historical_break_even.csv) reports every asset-date, policy, queue and latency case. The model does not turn thousands of correlated fills into thousands of independent observations.

![Historical break-even](figures/08_historical_break_even.png)

Historical figures and values are derived from Binance Vision data and distributed under [CC BY-NC-SA 4.0](../LICENSE-DATA.md). Their exact source URLs, full-archive SHA-256 checksums, scan counts, time windows and normalized-cache checksums are in [data_provenance.json](data_provenance.json). No raw exchange archive is included in the repository.
"""
    text += """
## Limits and what would change the conclusion

The replay cannot identify actual order-level queue position, reactively alter market impact, or model a complete live feed/gateway latency chain. The same-price priority rule and unknown deep-book depth are assumptions. Terminal flattening uses the last BBO and records any visible-depth overrun. Synthetic uncertainty is conditional on the chosen generator; historical coverage remains a few short windows. All four policies, all declared stress cells, and all selected dates are retained.

The next meaningful test is a frozen multiweek evaluation with order-level data, independently observed execution latency, and a prospectively chosen capital/risk objective. More elaborate policy names or a favorable single chart would not supply that evidence.
"""
    (output / "RESULTS.md").write_text(text)
    return output / "RESULTS.md"
