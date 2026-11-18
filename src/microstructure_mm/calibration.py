"""Past-window calibration, with zero-count depth bins retained."""

from dataclasses import asdict

import numpy as np
from scipy.optimize import minimize

from .types import Calibration, ReplayConfig, Tape


def poisson_depth_fit(depths: np.ndarray, counts: np.ndarray, exposure: float):
    """Fit E[N(delta)]=exposure*A*exp(-kappa*delta), including zero bins.

    Counts at successive depths are nested, so this is a composite Poisson
    likelihood, NOT independent sampling and NOT grounds for naive standard
    errors. Calibration quality is evaluated on subsequent time windows.
    """
    depths = np.asarray(depths, float)
    counts = np.asarray(counts, float)
    if (
        depths.ndim != 1
        or counts.shape != depths.shape
        or len(depths) < 2
        or np.any(depths < 0)
        or np.any(counts < 0)
        or exposure <= 0
        or not np.all(np.isfinite(depths))
        or not np.all(np.isfinite(counts))
    ):
        raise ValueError("invalid depths, counts or exposure")
    if counts.sum() == 0:
        return 1e-6, 1.0, {"success": False, "reason": "no_tail_trades", "nll": 0.0}
    scale = max(float(np.median(np.diff(np.unique(depths)))), 1e-3)
    x = depths / scale

    def objective(theta):
        log_mu = np.log(exposure) + theta[0] - np.exp(theta[1]) * x
        mu = np.exp(np.clip(log_mu, -700, 700))
        return float(np.sum(mu - counts * log_mu))

    opt = minimize(
        objective,
        [np.log(max(counts[0] / exposure, 1e-5)), np.log(0.5)],
        method="L-BFGS-B",
        bounds=[(-20, 15), (-9, 6)],
    )
    return (
        float(np.exp(opt.x[0])),
        float(np.exp(opt.x[1]) / scale),
        {"success": bool(opt.success), "reason": str(opt.message), "nll": float(opt.fun)},
    )


def calibrate(tape: Tape, config: ReplayConfig | None = None):
    config = ReplayConfig() if config is None else config
    quotes = [e for e in tape.events if e.kind == "quote"]
    if len(quotes) < 3 or tape.duration <= 0:
        raise ValueError("at least three quotes and positive duration are required")
    times = np.array([e.time for e in quotes])
    mids = np.array([(e.bid + e.ask) / 2 for e in quotes])
    grid = np.arange(times[0], times[-1] + 1e-10, config.variance_sample)
    ix = np.searchsorted(times, grid, side="right") - 1
    prices = mids[np.maximum(ix, 0)]
    variance = max(float(np.mean(np.diff(prices) ** 2) / config.variance_sample), 1e-4)
    spread = float(np.median([(e.ask - e.bid) / 2 for e in quotes]))
    # The trade uses only the quote already seen in message order.
    distances = []
    mid = mids[0]
    for e in tape.events:
        if e.kind == "quote":
            mid = 0.5 * (e.bid + e.ask)
        else:
            distances.append(max(0.0, e.side * (e.price - mid)))
    depths = np.arange(1, 9, dtype=float) * max(0.5, spread)
    counts = np.array([np.count_nonzero(np.asarray(distances) >= d) for d in depths])
    intensity, kappa, diagnostic = poisson_depth_fit(depths, counts, tape.duration)
    # Ex ante risk budget: at max inventory, reservation shift is 3 training
    # half-spreads when variance equals its training estimate and tau=horizon.
    gamma = 3.0 * spread / (config.max_inventory * variance * config.horizon)
    fit = Calibration(
        variance,
        kappa,
        intensity,
        spread,
        gamma,
        len(quotes),
        len(distances),
        times[0],
        tape.events[-1].time,
    )
    detail = {
        **diagnostic,
        "depths": depths.tolist(),
        "counts": counts.tolist(),
        "predicted_counts": (tape.duration * intensity * np.exp(-kappa * depths)).tolist(),
        "calibration": asdict(fit),
        "variance_observations": len(prices),
    }
    return fit, detail
