"""Read standard OHLC and MetaTrader 5 bar exports into the desk's closed-bar format."""

import csv
import io
import math
import re
import time
from datetime import datetime, timezone
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DURATIONS = {"15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
SYMBOLS = {"XAUUSD", "EURUSD", "GBPUSD"}
MAX_BARS = 3000


def _timestamp(fields, local_zone):
    value = fields["time"].strip()
    if fields.get("date"):
        value = fields["date"].strip() + " " + value
    if re.fullmatch(r"\d{10}", value):
        return int(value)
    value = re.sub(r"^(\d{4})\.(\d{2})\.(\d{2})", r"\1-\2-\3", value)
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        local = dt.replace(tzinfo=local_zone)
        if local.utcoffset() != local.replace(fold=1).utcoffset():
            raise ValueError("เวลาอยู่ในช่วงเปลี่ยน DST ให้ใส่ UTC offset ในคอลัมน์ time")
        dt = local
    return int(dt.timestamp())


def read_export(symbol, interval, zone_name, csv_text, now=None):
    if symbol not in SYMBOLS or interval not in DURATIONS:
        raise ValueError("กรุณาเลือกสินทรัพย์และกรอบเวลาที่รองรับ")
    if not isinstance(csv_text, str) or len(csv_text) > 1_000_000:
        raise ValueError("ไฟล์ CSV ต้องมีขนาดไม่เกิน 1 MB")
    try:
        zone = timezone.utc if zone_name == "UTC" else ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
        raise ValueError("เขตเวลาไม่ถูกต้อง ลองใช้ UTC หรือ Asia/Bangkok") from exc
    raw = csv_text.lstrip("\ufeff").strip()
    if not raw:
        raise ValueError("ไฟล์ CSV ว่าง")
    header = raw.splitlines()[0]
    delimiter = max(("\t", ",", ";"), key=header.count)
    reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter, strict=True)
    reader.fieldnames = [key.strip().lower().strip("<>") for key in reader.fieldnames or ()]
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError("ไฟล์ CSV มีชื่อคอลัมน์ซ้ำ")
    if not {"time", "open", "high", "low", "close"} <= set(reader.fieldnames):
        raise ValueError("ต้องมี time, open, high, low, close หรือหัวตารางจาก MT5")
    now = int(time.time()) if now is None else now
    seconds = DURATIONS[interval]
    bars = []
    removed = 0
    try:
        for i, row in enumerate(reader, start=2):
            if i > MAX_BARS + 1:
                raise ValueError(f"รองรับสูงสุด {MAX_BARS:,} แท่งต่อไฟล์")
            try:
                opened = _timestamp(row, zone)
                bar = {k: float(row[k]) for k in ("open", "high", "low", "close")}
                volume = float(row.get("tickvol") or row.get("volume") or 0)
                if (None in row or not all(math.isfinite(x) and 0 < x < 1e12 for x in bar.values())
                        or not math.isfinite(volume) or volume < 0
                        or bar["low"] > min(bar["open"], bar["close"])
                        or bar["high"] < max(bar["open"], bar["close"])):
                    raise ValueError("ราคา OHLC หรือปริมาณไม่ถูกต้อง")
                if opened > now:
                    raise ValueError("เวลาอยู่ในอนาคต ตรวจเขตเวลาของไฟล์")
            except (AttributeError, ValueError, OverflowError, OSError, TypeError) as exc:
                raise ValueError(f"ตรวจวันที่ เวลา และราคาในบรรทัด {i} ({exc})") from exc
            if opened + seconds > now:
                removed += 1
                continue
            bars.append(dict(bar, time=opened, volume=volume))
    except csv.Error as exc:
        raise ValueError("โครงสร้างไฟล์ CSV ไม่สมบูรณ์") from exc
    if len(bars) < 35:
        raise ValueError("ต้องมีแท่งที่ปิดแล้วอย่างน้อย 35 แท่ง")
    bars.sort(key=lambda bar: bar["time"])
    gaps = [b["time"] - a["time"] for a, b in zip(bars, bars[1:])]
    if min(gaps) <= 0:
        raise ValueError("พบเวลาแท่งซ้ำในไฟล์")
    if not 0.94 * seconds <= median(gaps) <= 1.06 * seconds:
        raise ValueError("กรอบเวลาที่เลือกไม่ตรงกับระยะห่างของแท่งส่วนใหญ่ในไฟล์")
    return dict(candles=bars, symbol=symbol, interval=interval, mode="import",
                source="CSV / MT5", stale=False, status="history", fetched_at=now,
                as_of=bars[-1]["time"] + seconds, source_time=None, session_closed=False,
                warning=f"กราฟย้อนหลังจากไฟล์ของคุณ • ตัดแท่งที่ยังเปิด {removed} แท่ง")
