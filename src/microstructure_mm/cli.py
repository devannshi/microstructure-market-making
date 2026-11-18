"""Command-line entry points; network access is explicit in `historical`."""

import argparse
import json
from pathlib import Path

from .experiments import run_historical, run_synthetic
from .plots import build_figures
from .report import build_report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Execution-aware market-making research")
    sub = parser.add_subparsers(dest="command", required=True)
    synth = sub.add_parser("research", help="run offline synthetic experiments")
    synth.add_argument("--quick", action="store_true")
    synth.add_argument("--trials", type=int)
    synth.add_argument("--stress-trials", type=int)
    synth.add_argument("--output", type=Path, default=Path("results"))
    real = sub.add_parser(
        "historical", help="download verified public archives and replay held-out blocks"
    )
    real.add_argument("--days", nargs="+", default=["2024-01-01", "2024-01-02", "2024-01-03"])
    real.add_argument("--symbols", nargs="+", default=["ETHUSDT", "BTCUSDT"])
    real.add_argument("--seconds", type=float, default=7200)
    real.add_argument("--output", type=Path, default=Path("results"))
    real.add_argument("--cache", type=Path, default=Path("data/cache"))
    plots = sub.add_parser("figures", help="regenerate figures from result tables")
    plots.add_argument("--output", type=Path, default=Path("results"))
    report = sub.add_parser("report", help="generate the research report from saved tables")
    report.add_argument("--output", type=Path, default=Path("results"))
    args = parser.parse_args(argv)
    if args.command == "research":
        if args.trials is not None and args.trials < 2:
            parser.error("--trials must be >=2")
        if args.stress_trials is not None and args.stress_trials < 2:
            parser.error("--stress-trials must be >=2")
        print(
            json.dumps(
                run_synthetic(args.output, args.quick, args.trials, args.stress_trials), indent=2
            )
        )
        print("Figures:", ", ".join(build_figures(args.output)))
    elif args.command == "historical":
        if args.seconds < 2700:
            parser.error("--seconds must be >=2700 to include training and one test block")
        print(
            json.dumps(
                run_historical(args.output, args.cache, args.days, args.seconds, args.symbols),
                indent=2,
            )
        )
        if all(
            (args.output / name).exists()
            for name in ("synthetic_trials.csv", "rank_stability.csv", "paired_comparisons.csv")
        ):
            build_figures(args.output)
    elif args.command == "report":
        print(build_report(args.output))
    else:
        print("Figures:", ", ".join(build_figures(args.output)))


if __name__ == "__main__":
    main()
