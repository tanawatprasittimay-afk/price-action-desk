"""Read-only spot FX and gold candles from Twelve Data, plus an offline fixture."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from http.client import HTTPException
from itertools import groupby
import json
import math
import os
import random
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SYMBOLS = ("XAUUSD", "EURUSD", "GBPUSD")
INTERVALS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
_API_INTERVAL = {"15m": "15min", "1h": "1h", "4h": "1h", "1d": "1h"}
_API_URL = "https://api.twelvedata.com/time_series"
_LIMIT = 600
_RESPONSE_LIMIT = 2_000_000
_DEMO_END = 1_735_689_600  # 2025-01-01 00:00:00 UTC, fixed across runs


class MarketUnavailable(RuntimeError):
    """No verified snapshot is available for the requested market."""


@dataclass
class _Entry:
    snapshot: dict | None = None
    retry_at: float = 0.0
    error: str | None = None


_locks = {(s, i): threading.Lock() for s in SYMBOLS for i in INTERVALS}
_cache: dict[tuple[str, str], _Entry] = {}


def _provider_error(code):
    if code in (401, 403):
        return "Twelve Data ไม่อนุญาตให้โหลดคู่นี้ กรุณาตรวจ TWELVE_DATA_API_KEY และสิทธิ์ข้อมูลของบัญชี"
    if code == 429:
        return "Twelve Data แจ้งว่าโควตาข้อมูลเต็ม กรุณารอรอบโควตาถัดไป"
    return "ติดต่อ Twelve Data หรืออ่านข้อมูลราคาจากผู้ให้บริการไม่ได้"


def _get_series(symbol, interval, limit, key):
    query = urlencode(dict(symbol=symbol, interval=interval, outputsize=limit, timezone="UTC", order="ASC"))
    req = Request(_API_URL + "?" + query, headers={"Authorization": "apikey " + key, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=8) as response:
            source_time = int(parsedate_to_datetime(response.headers["Date"]).timestamp())
            raw = response.read(_RESPONSE_LIMIT + 1)
        if len(raw) > _RESPONSE_LIMIT:
            raise ValueError("response too large")
        data = json.loads(raw)
    except HTTPError as exc:
        raise MarketUnavailable(_provider_error(exc.code)) from None
    except (OSError, HTTPException, ValueError, TypeError, KeyError, OverflowError):
        raise MarketUnavailable(_provider_error(None)) from None
    if not isinstance(data, dict):
        raise MarketUnavailable("ผู้ให้บริการส่งข้อมูลราคาผิดรูปแบบ")
    if data.get("status") == "error":
        raise MarketUnavailable(_provider_error(data.get("code")))
    meta = data.get("meta")
    if not isinstance(meta, dict) or meta.get("symbol") != symbol or meta.get("interval") != interval:
        raise MarketUnavailable("คู่เงินหรือกรอบเวลาในคำตอบของผู้ให้บริการไม่ตรงกับที่ขอ")
    return data.get("values"), source_time


def _parse_candles(rows, seconds, cutoff):
    candles = []
    try:
        if not isinstance(rows, list) or len(rows) > 5000:
            raise ValueError("unexpected response")
        previous = None
        for row in rows:
            dt = datetime.strptime(row["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            stamp = int(dt.timestamp())
            if stamp <= 0 or stamp % seconds or (previous is not None and stamp <= previous):
                raise ValueError("invalid candle time or order")
            previous = stamp
            if stamp + seconds > cutoff:
                continue
            opened, high, low, close = (float(row[field]) for field in ("open", "high", "low", "close"))
            # Spot FX has no centralized volume. The schema uses zero as a
            # placeholder and exposes has_volume=False to the presentation layer.
            if not all(math.isfinite(v) for v in (opened, high, low, close)):
                raise ValueError("non-finite OHLC")
            if not 0 < low <= min(opened, close) <= max(opened, close) <= high:
                raise ValueError("invalid OHLC")
            candles.append(dict(time=stamp, open=opened, high=high, low=low, close=close, volume=0.0))
        if not candles:
            raise ValueError("no closed candles")
    except (KeyError, ValueError, TypeError, OverflowError):
        raise MarketUnavailable("ข้อมูลแท่งราคาจากผู้ให้บริการไม่ครบหรือไม่สอดคล้องกัน") from None
    return candles


def _aggregate_utc(candles, seconds):
    """Build unambiguous UTC bars from complete sets of hourly candles.

    Vendor-native 4h bars shift with daylight saving and native 1d ignores
    timezone. Partial UTC buckets, including holiday gaps, stay out.
    """
    combined = []
    for period, grouped in groupby(candles, key=lambda bar: bar["time"] // seconds):
        bars = list(grouped)
        if len(bars) == seconds // 3600:  # unique, ordered UTC-aligned hourly inputs
            combined.append(dict(time=period * seconds, open=bars[0]["open"], high=max(b["high"] for b in bars),
                                 low=min(b["low"] for b in bars), close=bars[-1]["close"], volume=0.0))
    return combined


def _reference_session(symbol, timestamp):
    """Reference schedule including US daylight-saving transitions.

    https://www.oanda.com/bvi-en/cfds/hours-of-operation/
    Broker-specific holidays and early closures require a broker calendar.
    """
    try:
        local = datetime.fromtimestamp(timestamp, ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError:
        raise MarketUnavailable("ไม่พบเขตเวลานิวยอร์กสำหรับตรวจช่วงที่ตลาดปิด (ติดตั้ง tzdata)") from None
    minute = local.hour * 60 + local.minute
    close, reopen = 16 * 60 + 59, (18 if symbol == "XAUUSD" else 17) * 60 + 5
    weekday = local.weekday()
    if weekday == 5 or (weekday == 4 and minute >= close) or (weekday == 6 and minute < reopen):
        return "weekend"
    if close <= minute < reopen:
        return "rollover"
    return None


def _overlaps_weekend(symbol, start, seconds):
    # All supported intervals are <= 24h, shorter than the weekly closure.
    # Intersection therefore requires at least one endpoint in the closure.
    # Use the final included second because candle boundaries are half open.
    return (_reference_session(symbol, start) == "weekend" or
            _reference_session(symbol, start + seconds - 1) == "weekend")


def _fetch(symbol, interval):
    key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    public_trial = not key or key == "demo"
    if public_trial and symbol != "EURUSD":
        raise MarketUnavailable("ข้อมูลจริงคู่นี้ต้องใช้ TWELVE_DATA_API_KEY ที่มีสิทธิ์เข้าถึง หรือนำเข้า CSV จากโบรกเกอร์")
    provider_symbol = f"{symbol[:3]}/{symbol[3:]}"
    api_interval = _API_INTERVAL[interval]
    source_seconds = 900 if interval == "15m" else 3600
    ratio = INTERVALS[interval] // source_seconds
    count = min(5000, (_LIMIT + 2) * ratio)
    rows, source_time = _get_series(provider_symbol, api_interval, count, key or "demo")
    fetched = int(time.time())
    candles = _parse_candles(rows, source_seconds, min(fetched, source_time))
    if ratio > 1:
        candles = _aggregate_utc(candles, INTERVALS[interval])
    seconds = INTERVALS[interval]
    complete = len(candles)
    candles = [c for c in candles if not _overlaps_weekend(symbol, c["time"], seconds)]
    excluded = complete - len(candles)
    if not candles:
        raise MarketUnavailable("ยังไม่มีแท่งปิดครบตามกรอบเวลาที่อยู่นอกช่วงตลาดปิดสุดสัปดาห์")
    source = "Twelve Data (EUR/USD public trial)" if public_trial else "Twelve Data Spot"
    return dict(symbol=symbol, interval=interval, mode="live", source=source, public_trial=public_trial,
                source_time=source_time, fetched_at=fetched, as_of=candles[-1]["time"] + INTERVALS[interval],
                excluded_weekend_bars=excluded, has_volume=False, candles=candles[-_LIMIT:])


def _session_warning(symbol, now):
    if _reference_session(symbol, now):
        return "อยู่ในช่วงพักตามเวลาตลาดอ้างอิงนิวยอร์ก ตรวจเวลาซื้อขายจริงของโบรกเกอร์"
    return None


@lru_cache(maxsize=len(SYMBOLS) * len(INTERVALS))
def _demo(symbol, interval):
    rng = random.Random(f"PriceActionDesk/1/{symbol}/{interval}")
    seconds = INTERVALS[interval]
    price = {"XAUUSD": 2670.0, "EURUSD": 1.0860, "GBPUSD": 1.2700}[symbol]
    movement = {"15m": 0.00055, "1h": 0.0011, "4h": 0.0022, "1d": 0.005}[interval]
    if symbol == "XAUUSD":
        movement *= 1.8
    candles = []
    for index in range(_LIMIT):
        opened = price
        change = rng.gauss(0.22 * math.sin(index / 12), 1.0) * movement
        price = opened * math.exp(change)
        high = max(opened, price) * (1 + rng.random() * movement)
        low = min(opened, price) * (1 - rng.random() * movement)
        candles.append(dict(time=_DEMO_END - (_LIMIT - index) * seconds, open=opened, high=high,
                            low=low, close=price, volume=0.0))
    return dict(symbol=symbol, interval=interval, mode="demo", source="ข้อมูลจำลอง", status="demo", stale=False,
                public_trial=False, source_time=None, as_of=_DEMO_END, fetched_at=None, warning=None,
                session_closed=False, has_volume=False, candles=candles)


def load_market(symbol, interval, mode="live"):
    """All timestamps are UTC Unix seconds. as_of is the exclusive closing boundary."""
    if symbol not in SYMBOLS or interval not in INTERVALS or mode not in ("live", "demo"):
        raise ValueError("คู่เงิน กรอบเวลา หรือโหมดข้อมูลไม่รองรับ")
    if mode == "demo":
        return deepcopy(_demo(symbol, interval))
    key = (symbol, interval)
    seconds = INTERVALS[interval]
    with _locks[key]:
        entry = _cache.setdefault(key, _Entry())
        if time.monotonic() >= entry.retry_at:
            try:
                entry.snapshot = _fetch(symbol, interval)
                entry.error = None
                now = time.time()
                # One request per closed bar, including across concurrent viewers.
                fresh_for = seconds - now % seconds
                expected_close = int(now) // seconds * seconds
                if entry.snapshot["as_of"] < expected_close and not _overlaps_weekend(symbol, expected_close - seconds, seconds):
                    fresh_for = min(60, fresh_for)
            except MarketUnavailable as exc:
                entry.error = str(exc)
                fresh_for = 60
            entry.retry_at = time.monotonic() + fresh_for
        if entry.snapshot is None:
            raise MarketUnavailable(entry.error or "ยังไม่มีข้อมูลจากผู้ให้บริการ")
        data = deepcopy(entry.snapshot)
        now = int(time.time())
        issue = entry.error
        if not issue and data["as_of"] != now // seconds * seconds:
            issue = "แท่งปิดล่าสุดยังไม่ถึงรอบปัจจุบัน อาจเป็นช่วงตลาดพักหรือข้อมูลล่าช้า"
        if not issue and abs(data["source_time"] - data["fetched_at"]) > 120:
            issue = "เวลาของผู้ให้บริการกับเวลาของเครื่องต่างกันมากกว่า 2 นาที"
        session = _session_warning(symbol, now)
        trial = "ใช้ช่องข้อมูลทดลองสาธารณะ EUR/USD ของ Twelve Data" if data["public_trial"] else None
        history_note = (f"ตัด {data['excluded_weekend_bars']} แท่งที่ทับช่วงปิดสุดสัปดาห์นิวยอร์ก "
                        "ช่วงพักรายวันและวันหยุดอาจต่างจากโบรกเกอร์")
        warning = " · ".join(filter(None, (issue, session, trial, history_note)))
        data.update(warning=warning, session_closed=bool(session), stale=bool(issue), status="stale" if issue else "fresh")
        return data
