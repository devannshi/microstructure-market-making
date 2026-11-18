"""Typed contracts. Prices are ticks; quantities are lots; time is seconds."""

import math
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class Event:
    time: float
    kind: str  # quote or trade
    bid: float = 0.0
    ask: float = 0.0
    bid_size: float = 0.0
    ask_size: float = 0.0
    price: float = 0.0
    size: float = 0.0
    side: int = 0  # aggressive buy +1 / aggressive sell -1

    def __post_init__(self):
        vals = (self.time, self.bid, self.ask, self.bid_size, self.ask_size, self.price, self.size)
        if not all(math.isfinite(x) for x in vals) or self.time < 0:
            raise ValueError("event values must be finite; time must be nonnegative")
        if self.kind == "quote":
            if self.bid <= 0 or self.ask <= self.bid or min(self.bid_size, self.ask_size) < 0:
                raise ValueError("quote requires positive uncrossed prices and nonnegative sizes")
        elif self.kind == "trade":
            if self.price <= 0 or self.size <= 0 or self.side not in (-1, 1):
                raise ValueError("trade requires positive price/size and side +/-1")
        else:
            raise ValueError("event kind must be quote or trade")


@dataclass(slots=True)
class Tape:
    events: list[Event]
    name: str = "tape"
    tick_size: float = 1.0
    lot_size: float = 1.0
    source: str = "synthetic"
    session_end: float | None = None

    def __post_init__(self):
        if not self.events:
            raise ValueError("empty tape")
        if self.events[0].kind != "quote":
            raise ValueError("the tape must start with a quote")
        if any(b.time < a.time for a, b in zip(self.events, self.events[1:])):
            raise ValueError("events must be sorted; equal-time order is retained")
        if self.session_end is None:
            self.session_end = self.events[-1].time
        if not math.isfinite(self.session_end) or self.session_end < self.events[-1].time:
            raise ValueError("session_end must be finite and no earlier than the last event")
        if self.tick_size <= 0 or self.lot_size <= 0:
            raise ValueError("tick_size and lot_size must be positive")

    @property
    def duration(self):
        return self.session_end - self.events[0].time

    def window(self, start: float, end: float) -> "Tape":
        """Half-open interval with the latest pre-start quote; no future initial book."""
        if start < 0 or end <= start:
            raise ValueError("require 0 <= start < end")
        previous = None
        selected = []
        for event in self.events:
            if event.time < start:
                if event.kind == "quote":
                    previous = event
                continue
            if event.time >= end:
                break
            if not selected:
                if previous is not None:
                    selected.append(
                        Event(
                            0.0,
                            "quote",
                            previous.bid,
                            previous.ask,
                            previous.bid_size,
                            previous.ask_size,
                        )
                    )
                elif event.kind != "quote":
                    continue
            d = asdict(event)
            d["time"] = event.time - start
            selected.append(Event(**d))
        if not selected:
            raise ValueError("requested window contains no usable events")
        return Tape(
            selected,
            f"{self.name}[{start:g},{end:g})",
            self.tick_size,
            self.lot_size,
            self.source,
            end - start,
        )


@dataclass(frozen=True)
class ReplayConfig:
    latency: float = 0.05
    refresh: float = 1.0
    maker_fee_bps: float = 0.0
    taker_fee_bps: float = 1.0
    queue: str = "back"
    queue_multiplier: float = 1.0
    max_inventory: float = 10.0
    order_size: float = 1.0
    participation: float = 0.05
    horizon: float = 30.0
    variance_half_life: float = 30.0
    variance_sample: float = 1.0
    trace_interval: float = 1.0
    retain_priority: bool = True

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
        if self.latency < 0 or self.queue_multiplier < 0:
            raise ValueError("latency and queue multiplier must be nonnegative")
        if self.queue not in ("front", "back"):
            raise ValueError("queue must be front or back")
        if (
            min(
                self.refresh,
                self.max_inventory,
                self.order_size,
                self.horizon,
                self.variance_half_life,
                self.variance_sample,
                self.trace_interval,
            )
            <= 0
        ):
            raise ValueError("intervals, sizes, inventory and horizon must be positive")
        if not 0 < self.participation <= 1:
            raise ValueError("participation must be in (0, 1]")
        if self.order_size > self.max_inventory:
            raise ValueError("order_size cannot exceed max_inventory")


@dataclass(frozen=True)
class Calibration:
    variance_rate: float
    kappa: float
    arrival_rate: float
    half_spread: float
    gamma: float
    n_quotes: int
    n_trades: int
    start: float
    end: float
    method: str = "binned_poisson_composite_likelihood"

    def __post_init__(self):
        if (
            min(self.variance_rate, self.kappa, self.arrival_rate, self.half_spread, self.gamma)
            <= 0
        ):
            raise ValueError("calibrated parameters must be positive")
        if not all(
            np.isfinite(v)
            for v in (
                self.variance_rate,
                self.kappa,
                self.arrival_rate,
                self.half_spread,
                self.gamma,
            )
        ):
            raise ValueError("calibrated parameters must be finite")
