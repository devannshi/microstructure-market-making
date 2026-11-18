"""Verified public-data ingestion and a neutral CSV interchange format."""

import csv
import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from .types import Event, Tape

BINANCE_BASE = "https://data.binance.vision/data/futures/um/daily"
TERMS_URL = "https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_archive(symbol: str, day: str, kind: str, cache: Path) -> tuple[Path, dict]:
    if kind not in ("bookTicker", "aggTrades") or not symbol.isalnum():
        raise ValueError("unsupported archive kind or symbol")
    datetime.strptime(day, "%Y-%m-%d")
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    name = f"{symbol}-{kind}-{day}.zip"
    url = f"{BINANCE_BASE}/{kind}/{symbol}/{name}"
    path = cache / name
    checksum_response = requests.get(url + ".CHECKSUM", timeout=30)
    checksum_response.raise_for_status()
    expected = checksum_response.text.split()[0].lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("invalid upstream checksum")
    if path.exists() and sha256(path) != expected:
        raise ValueError(f"cached checksum mismatch: remove {path} and retry")
    if not path.exists():
        temporary = path.with_suffix(".download")
        try:
            with requests.get(url, stream=True, timeout=(30, 90)) as r:
                r.raise_for_status()
                with temporary.open("wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            if sha256(temporary) != expected:
                raise ValueError("download checksum does not match upstream")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
    return path, {
        "url": url,
        "checksum_url": url + ".CHECKSUM",
        "sha256": expected,
        "bytes": path.stat().st_size,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "terms": TERMS_URL,
        "data_license": "CC-BY-NC-SA-4.0",
    }


def write_tape(tape: Tape, path: Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([asdict(e) for e in tape.events]).to_csv(path, index=False)
    path.with_suffix(".meta.json").write_text(
        json.dumps(
            {
                "name": tape.name,
                "tick_size": tape.tick_size,
                "lot_size": tape.lot_size,
                "source": tape.source,
                "session_end": tape.session_end,
            },
            indent=2,
        )
    )


def read_tape(
    path: Path,
    tick_size: float | None = None,
    lot_size: float | None = None,
    session_end: float | None = None,
) -> Tape:
    path = Path(path)
    metadata_path = path.with_suffix(".meta.json")
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    tick_size = metadata.get("tick_size", 1.0) if tick_size is None else tick_size
    lot_size = metadata.get("lot_size", 1.0) if lot_size is None else lot_size
    session_end = metadata.get("session_end") if session_end is None else session_end
    with path.open(newline="") as f:
        events = [
            Event(
                time=float(r["time"]),
                kind=r["kind"],
                **{k: float(r[k]) for k in ("bid", "ask", "bid_size", "ask_size", "price", "size")},
                side=int(r["side"]),
            )
            for r in csv.DictReader(f)
        ]
    return Tape(
        events,
        metadata.get("name", path.stem),
        tick_size,
        lot_size,
        metadata.get("source", "user CSV"),
        session_end,
    )


# Streaming ingestion keeps full raw archives off disk, including large stress days.
def _stream_window(
    symbol: str, day: str, kind: str, cache: Path, cutoff_ms: int, start_ms: int = 0
):
    import io

    import numpy as np
    from stream_unzip import stream_unzip

    window_stem = f"{symbol}-{kind}-{day}-{start_ms}-{cutoff_ms}.window"
    window_path = cache / f"{window_stem}.csv.gz"
    window_meta = cache / f"{window_stem}.json"
    if window_path.exists() and window_meta.exists():
        metadata = json.loads(window_meta.read_text())
        if sha256(window_path) != metadata["window_sha256"]:
            raise ValueError("raw-window cache checksum mismatch")
        return pd.read_csv(window_path), metadata
    name = f"{symbol}-{kind}-{day}.zip"
    url = f"{BINANCE_BASE}/{kind}/{symbol}/{name}"
    checksum_response = requests.get(url + ".CHECKSUM", timeout=30)
    checksum_response.raise_for_status()
    expected = checksum_response.text.split()[0].lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise ValueError("invalid upstream checksum")
    digest, byte_count = hashlib.sha256(), 0
    local_archive = cache / name

    def zipped_chunks():
        nonlocal byte_count
        if local_archive.exists():
            with local_archive.open("rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
                    byte_count += len(chunk)
                    yield chunk
        else:
            with requests.get(url, stream=True, timeout=(30, 120)) as r:
                r.raise_for_status()
                for chunk in r.iter_content(1 << 20):
                    if chunk:
                        digest.update(chunk)
                        byte_count += len(chunk)
                        yield chunk

    class IteratorReader(io.RawIOBase):
        def __init__(self, iterable):
            self.iterator = iter(iterable)
            self.pending = b""

        def readable(self):
            return True

        def readinto(self, buffer):
            if not self.pending:
                self.pending = next(self.iterator, b"")
            count = min(len(buffer), len(self.pending))
            buffer[:count] = self.pending[:count]
            self.pending = self.pending[count:]
            return count

    time_col = "transaction_time" if kind == "bookTicker" else "transact_time"
    selected, rows_scanned, regressions, previous_time = [], 0, 0, None
    chunks = zipped_chunks()
    members = 0
    for file_name, _, unzipped in stream_unzip(chunks):
        if not file_name.endswith(b".csv"):
            for _ in unzipped:
                pass
            continue
        members += 1
        reader = io.BufferedReader(IteratorReader(unzipped))
        for chunk in pd.read_csv(reader, chunksize=200_000):
            times = chunk[time_col].to_numpy()
            if previous_time is not None and len(times):
                regressions += int(times[0] < previous_time)
            regressions += int(np.count_nonzero(np.diff(times) < 0))
            if len(times):
                previous_time = times[-1]
            rows_scanned += len(chunk)
            window = chunk.loc[(chunk[time_col] < cutoff_ms) & (chunk[time_col] >= start_ms)]
            if not window.empty:
                selected.append(window)
        reader.close()
    for _ in chunks:
        pass
    if members != 1 or digest.hexdigest() != expected:
        raise ValueError("archive member count or full-archive SHA-256 verification failed")
    if not selected:
        raise ValueError("no observations in the requested archive window")
    source = {
        "url": url,
        "checksum_url": url + ".CHECKSUM",
        "sha256": expected,
        "bytes": byte_count,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(),
        "terms": TERMS_URL,
        "data_license": "CC-BY-NC-SA-4.0",
        "rows_scanned": rows_scanned,
        "timestamp_regressions": regressions,
        "integrity": "entire archive streamed, CRC and SHA-256 verified",
    }
    selected_frame = pd.concat(selected, ignore_index=True)
    selected_frame.to_csv(window_path, index=False, compression="gzip")
    source["window_sha256"] = sha256(window_path)
    window_meta.write_text(json.dumps(source, indent=2))
    return selected_frame, source


def load_binance_streaming(
    symbol: str,
    day: str,
    cache: Path,
    seconds: float = 7200,
    tick_size: float = 0.01,
    lot_size: float = 0.001,
    start_seconds: float = 0,
):
    """Verified full-archive streaming; cache only the selected normalized window."""
    import numpy as np

    if (
        not symbol.isalnum()
        or not 0 < seconds <= 86400
        or start_seconds < 0
        or start_seconds + seconds > 86400
    ):
        raise ValueError("invalid symbol or seconds")
    start_ms = int(
        datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000
    )
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    start_ms += int(start_seconds * 1000)
    stem = f"{symbol}-{day}-{seconds:g}s-tick{tick_size:g}-lot{lot_size:g}"
    if start_seconds:
        stem += f"-start{start_seconds:g}"
    stem += "-v2"
    normalized, metadata_path = cache / f"{stem}.npz", cache / f"{stem}.json"
    if normalized.exists() and metadata_path.exists():
        metadata = json.loads(metadata_path.read_text())
        if sha256(normalized) != metadata["normalized_sha256"]:
            raise ValueError("normalized cache checksum mismatch")
        with np.load(normalized, allow_pickle=False) as loaded:
            a = loaded["events"]
        events = [
            Event(float(r[0]), "quote" if r[1] == 0 else "trade", *map(float, r[2:8]), int(r[8]))
            for r in a
        ]
        return Tape(
            events, f"{symbol}-{day}", tick_size, lot_size, "Binance Vision", seconds
        ), metadata
    q, q_source = _stream_window(
        symbol, day, "bookTicker", cache, start_ms + int(seconds * 1000), start_ms
    )
    tr, tr_source = _stream_window(
        symbol, day, "aggTrades", cache, start_ms + int(seconds * 1000), start_ms
    )
    valid = (q.best_bid_price > 0) & (q.best_ask_price > q.best_bid_price)
    invalid_quotes = int((~valid).sum())
    q = q.loc[valid]
    off_grid_quotes = int(
        (
            (np.abs(q.best_bid_price / tick_size - np.rint(q.best_bid_price / tick_size)) > 1e-6)
            | (np.abs(q.best_ask_price / tick_size - np.rint(q.best_ask_price / tick_size)) > 1e-6)
        ).sum()
    )
    events = []
    for r in q.itertuples(index=False):
        events.append(
            Event(
                (r.transaction_time - start_ms) / 1000,
                "quote",
                round(r.best_bid_price / tick_size, 8),
                round(r.best_ask_price / tick_size, 8),
                r.best_bid_qty / lot_size,
                r.best_ask_qty / lot_size,
            )
        )
    for r in tr.itertuples(index=False):
        is_maker = (
            r.is_buyer_maker
            if isinstance(r.is_buyer_maker, bool)
            else str(r.is_buyer_maker).lower() == "true"
        )
        events.append(
            Event(
                (r.transact_time - start_ms) / 1000,
                "trade",
                price=round(r.price / tick_size, 8),
                size=r.quantity / lot_size,
                side=-1 if is_maker else 1,
            )
        )
    events.sort(key=lambda e: (e.time, 0 if e.kind == "trade" else 1))
    first_quote = next(i for i, e in enumerate(events) if e.kind == "quote")
    events = events[first_quote:]
    metadata = {
        "symbol": symbol,
        "day": day,
        "seconds_requested": seconds,
        "start_seconds_utc": start_seconds,
        "events": len(events),
        "quotes": len(q),
        "trades": len(tr),
        "invalid_quotes_removed": invalid_quotes,
        "off_grid_quotes_preserved": off_grid_quotes,
        "cache_schema": 2,
        "tick_size": tick_size,
        "lot_size": lot_size,
        "timestamp": "exchange transaction_time milliseconds",
        "same_timestamp_order": "trades then quotes, stable within each stream",
        "sources": [q_source, tr_source],
    }
    array = np.fromiter(
        (
            v
            for e in events
            for v in (
                e.time,
                0 if e.kind == "quote" else 1,
                e.bid,
                e.ask,
                e.bid_size,
                e.ask_size,
                e.price,
                e.size,
                e.side,
            )
        ),
        dtype=np.float64,
        count=9 * len(events),
    ).reshape(-1, 9)
    np.savez_compressed(normalized, events=array)
    metadata["normalized_sha256"] = sha256(normalized)
    metadata["normalized_bytes"] = normalized.stat().st_size
    metadata_path.write_text(json.dumps(metadata, indent=2))
    return Tape(events, f"{symbol}-{day}", tick_size, lot_size, "Binance Vision", seconds), metadata
