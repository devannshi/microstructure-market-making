import hashlib
import io
import zipfile
from dataclasses import replace

import pytest

from microstructure_mm.calibration import calibrate
from microstructure_mm.data import _stream_window
from microstructure_mm.engine import replay
from microstructure_mm.synthetic import SyntheticConfig, generate
from microstructure_mm.types import Calibration, Event, ReplayConfig, Tape


def test_record_flag_cannot_change_outcomes():
    tape = generate(12, SyntheticConfig(duration=20))
    fit, _ = calibrate(tape.window(0, 5))
    a = replay(tape.window(5, 20), "as_adaptive", fit, record=True)
    b = replay(tape.window(5, 20), "as_adaptive", fit, record=False)
    assert a.summary == b.summary


def test_fee_changes_accounting_but_not_fills():
    tape = generate(1, SyntheticConfig(duration=40))
    fit, _ = calibrate(tape.window(0, 10))
    cfg = ReplayConfig()
    a = replay(tape.window(10, 40), "as_fixed", fit, cfg)
    b = replay(tape.window(10, 40), "as_fixed", fit, replace(cfg, maker_fee_bps=1.5))
    assert a.summary["maker_fills"] == b.summary["maker_fills"]
    assert a.summary["maker_notional_ticks_lots"] == b.summary["maker_notional_ticks_lots"]
    assert a.summary["net_pnl_ticks_lots"] - b.summary["net_pnl_ticks_lots"] == pytest.approx(
        1.5 / 10000 * a.summary["maker_notional_ticks_lots"]
    )


def test_accounting_decomposition_closes():
    tape = generate(2, SyntheticConfig(duration=30))
    fit, _ = calibrate(tape.window(0, 10))
    r = replay(tape.window(10, 30), "as_fixed", fit, ReplayConfig(maker_fee_bps=1))
    s = r.summary
    assert s["net_pnl_ticks_lots"] == pytest.approx(
        s["spread_capture_ticks_lots"]
        + s["inventory_revaluation_ticks_lots"]
        - s["fees_ticks_lots"]
        - s["liquidation_spread_cost"]
    )


def test_unchanged_quote_retains_queue_priority():
    tape = Tape(
        [
            Event(0, "quote", 100, 102, 10, 10),
            Event(0.5, "trade", price=100, size=6, side=-1),
            Event(1, "quote", 100, 102, 10, 10),
            Event(1.5, "trade", price=100, size=6, side=-1),
            Event(2, "quote", 100, 102, 10, 10),
        ]
    )
    fit = Calibration(1, 1, 1, 1, 0.01, 3, 2, 0, 1)
    cfg = ReplayConfig(latency=0, refresh=1, participation=1, order_size=3, queue="back")
    a = replay(tape, "passive", fit, cfg)
    b = replay(tape, "passive", fit, replace(cfg, retain_priority=False))
    assert a.summary["maker_volume_lots"] == 2
    assert b.summary["maker_volume_lots"] == 0


def test_liquidation_depth_excess_is_flagged():
    tape = Tape(
        [
            Event(0, "quote", 100, 102, 10, 10),
            Event(1, "trade", price=100, size=20, side=-1),
            Event(2, "quote", 100, 102, 1, 1),
        ]
    )
    fit = Calibration(1, 1, 1, 1, 0.01, 3, 2, 0, 1)
    cfg = ReplayConfig(latency=0, refresh=10, participation=1, order_size=3, queue="front")
    assert replay(tape, "passive", fit, cfg).summary["liquidation_exceeds_visible_depth"]


def fake_zip(text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("data.csv", text)
    return buffer.getvalue()


class Response:
    def __init__(self, body):
        self.body = body
        self.text = body.decode() if isinstance(body, bytes) and len(body) < 100 else ""

    def raise_for_status(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, n):
        for i in range(0, len(self.body), 17):
            yield self.body[i : i + 17]


def test_streaming_verifies_full_archive_and_keeps_late_timestamp_regression(tmp_path, monkeypatch):
    text = "transaction_time,best_bid_price,best_ask_price,best_bid_qty,best_ask_qty\n1000,100,102,5,6\n3000,101,103,5,6\n1500,102,104,5,6\n"
    payload = fake_zip(text)
    digest = hashlib.sha256(payload).hexdigest()

    def get(url, **kwargs):
        return Response((digest + " data.zip").encode() if url.endswith("CHECKSUM") else payload)

    monkeypatch.setattr("microstructure_mm.data.requests.get", get)
    frame, metadata = _stream_window("ETHUSDT", "2024-01-01", "bookTicker", tmp_path, 2000, 1000)
    assert list(frame.transaction_time) == [1000, 1500]
    assert metadata["rows_scanned"] == 3 and metadata["timestamp_regressions"] == 1
    assert metadata["sha256"] == digest and metadata["bytes"] == len(payload)
    assert not list(tmp_path.glob("*.zip"))
    assert len(list(tmp_path.glob("*.window.csv.gz"))) == 1


def test_streaming_checksum_mismatch_rejected(tmp_path, monkeypatch):
    payload = fake_zip("transaction_time\n1000\n")

    def get(url, **kwargs):
        return Response(("0" * 64).encode() if url.endswith("CHECKSUM") else payload)

    monkeypatch.setattr("microstructure_mm.data.requests.get", get)
    with pytest.raises(ValueError, match="SHA-256"):
        _stream_window("ETHUSDT", "2024-01-01", "bookTicker", tmp_path, 2000)


def test_subtick_observations_are_preserved_but_new_quotes_stay_on_grid(tmp_path, monkeypatch):
    from microstructure_mm.data import load_binance_streaming
    from microstructure_mm.policies import Policy, quote_prices

    start = 1704067200000
    quotes = f"transaction_time,best_bid_price,best_ask_price,best_bid_qty,best_ask_qty\n{start},44582.2,44582.24,1,1\n{start + 1000},44582.2,44582.3,1,1\n"
    trades = f"transact_time,price,quantity,is_buyer_maker\n{start + 500},44582.24,0.001,false\n"
    payloads = {"bookTicker": fake_zip(quotes), "aggTrades": fake_zip(trades)}

    def get(url, **kwargs):
        kind = "bookTicker" if "bookTicker" in url else "aggTrades"
        body = payloads[kind]
        if url.endswith("CHECKSUM"):
            return Response(hashlib.sha256(body).hexdigest().encode())
        return Response(body)

    monkeypatch.setattr("microstructure_mm.data.requests.get", get)
    tape, metadata = load_binance_streaming(
        "BTCUSDT", "2024-01-01", tmp_path, 2, tick_size=0.1, lot_size=0.001
    )
    first = tape.events[0]
    assert first.bid == pytest.approx(445822.0)
    assert first.ask == pytest.approx(445822.4)
    assert metadata["off_grid_quotes_preserved"] == 1
    fit = Calibration(1, 1, 1, 1, 0.01, 3, 2, 0, 1)
    for policy in Policy:
        b, a = quote_prices(policy, first.bid, first.ask, 0, 1, 30, fit, ReplayConfig())
        assert b == int(b) and a == int(a) and b < first.ask and a > first.bid
    again, cache_metadata = load_binance_streaming(
        "BTCUSDT", "2024-01-01", tmp_path, 2, tick_size=0.1, lot_size=0.001
    )
    assert again.events == tape.events
    assert cache_metadata["normalized_sha256"] == metadata["normalized_sha256"]


def test_explicit_session_end_integrates_unobserved_gap_without_future_prices():
    tape = Tape(
        [Event(0, "quote", 100, 102, 10, 10), Event(1, "trade", price=100, size=20, side=-1)],
        session_end=5,
    )
    fit = Calibration(1, 1, 1, 1, 0.01, 3, 2, 0, 1)
    cfg = ReplayConfig(
        latency=0, refresh=10, participation=1, order_size=3, queue="front", taker_fee_bps=0
    )
    result = replay(tape, "passive", fit, cfg)
    assert result.summary["duration_s"] == 5
    assert result.summary["rms_inventory"] == pytest.approx((9 * 4 / 5) ** 0.5)
    assert result.fills.iloc[-1].time == 5


def test_window_end_is_scheduled_not_the_last_observed_timestamp():
    tape = Tape(
        [Event(0, "quote", 100, 102, 10, 10), Event(2, "quote", 101, 103, 10, 10)], session_end=10
    )
    window = tape.window(0, 7)
    assert window.session_end == 7 and window.duration == 7
    assert window.events[-1].time == 2
