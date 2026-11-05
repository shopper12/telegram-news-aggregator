"""Event-driven equity crisis monitor. No trade execution or fabricated real-time quotes."""
from __future__ import annotations

import argparse
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
STATE_PATH = Path(os.getenv("CRISIS_STATE_PATH", "reports/crisis_watch_state.json"))
DEFAULT_SYMBOLS = {
    "SPY": ("미국 S&P500 ETF", "US", "index"),
    "QQQ": ("미국 나스닥100 ETF", "US", "index"),
    "SOXX": ("미국 반도체 ETF", "US", "sector"),
    "IWM": ("미국 중소형 ETF", "US", "sector"),
    "HYG": ("미국 하이일드채", "US", "cross"),
    "TLT": ("미국 장기채 ETF", "US", "cross"),
    "GLD": ("금 ETF", "US", "cross"),
    "NVDA": ("엔비디아", "US", "stock"),
    "TSLA": ("테슬라", "US", "stock"),
    "EWY": ("미국 상장 한국 ETF", "US", "cross"),
    "^KS11": ("코스피", "KR", "index"),
    "^KQ11": ("코스닥", "KR", "index"),
    "005930.KS": ("삼성전자", "KR", "stock"),
    "000660.KS": ("SK하이닉스", "KR", "stock"),
}

def _num(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None

def _pct(current: float, previous: float | None) -> float | None:
    return round((current / previous - 1) * 100, 3) if previous and previous > 0 else None

def is_open(region: str, now: datetime) -> bool:
    local = now.astimezone(NY if region == "US" else KST)
    minute = local.hour * 60 + local.minute
    if local.weekday() >= 5:
        return False
    # Trading holidays and exchange halts require a licensed calendar / data vendor.
    return (570 <= minute < 960) if region == "US" else (540 <= minute < 930)

def configured_symbols() -> dict[str, tuple[str, str, str]]:
    symbols = dict(DEFAULT_SYMBOLS)
    extra = os.getenv("CRISIS_EXTRA_SYMBOLS", "")
    for symbol in extra.split(","):
        s = symbol.strip().upper()
        if s and s not in symbols and len(s) < 25:
            symbols[s] = (s, "KR" if s.endswith((".KS", ".KQ")) else "US", "stock")
    return symbols

def _lagged_return(bars: list[tuple[int, float]], window_seconds: int) -> float | None:
    if len(bars) < 2:
        return None
    latest_time, latest_price = bars[-1]
    target = latest_time - window_seconds
    i = bisect_right([row[0] for row in bars], target) - 1
    if i < 0 or target - bars[i][0] > 150:
        return None
    return _pct(latest_price, bars[i][1])

def fetch_quote(symbol: str, name: str, region: str, kind: str, now: datetime) -> dict[str, Any]:
    base = {"symbol": symbol, "name": name, "region": region, "kind": kind, "source": "Yahoo Finance intraday (unofficial, potentially delayed)", "price": None, "age_sec": None, "error": None}
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}?range=1d&interval=1m&includePrePost=false"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MarketCrisisWatch/1.0)"}, timeout=6)
        response.raise_for_status()
        result = (response.json().get("chart", {}).get("result") or [None])[0]
        if not isinstance(result, dict):
            raise ValueError("no_chart_result")
        meta = result.get("meta") or {}
        raw = (result.get("indicators", {}).get("quote") or [{}])[0]
        bars = []
        for ts, value in zip(result.get("timestamp") or [], raw.get("close") or []):
            v = _num(value)
            if v is not None and v > 0:
                bars.append((int(ts), v))
        if not bars:
            raise ValueError("no_intraday_bars")
        bars.sort()
        ts, price = bars[-1]
        age = max(0, int(now.timestamp() - ts))
        previous = _num(meta.get("previousClose")) or _num(meta.get("chartPreviousClose"))
        return {**base, "price": price, "at": datetime.fromtimestamp(ts, UTC).isoformat(),
                "age_sec": age, "fresh": age <= int(os.getenv("CRISIS_MAX_LAG_SEC", "420")),
                "open": is_open(region, now), "day_pct": _pct(price, previous),
                "m1_pct": _lagged_return(bars, 60), "m5_pct": _lagged_return(bars, 300),
                "m15_pct": _lagged_return(bars, 900)}
    except Exception as exc:
        return {**base, "fresh": False, "open": is_open(region, now), "error": f"{type(exc).__name__}: {str(exc)[:100]}"}

def scan(now: datetime | None = None, symbols: dict[str, tuple[str, str, str]] | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    symbols = symbols or configured_symbols()
    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=min(8, len(symbols)), thread_name_prefix="crisis-quote") as pool:
        futures = {pool.submit(fetch_quote, symbol, *details, now): symbol for symbol, details in symbols.items()}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                results[symbol] = future.result()
            except Exception as exc:
                results[symbol] = {"symbol": symbol, "fresh": False, "error": type(exc).__name__}
    return {"checked_at": now.astimezone(KST).isoformat(timespec="seconds"), "provider": "Yahoo Finance, no real-time guarantee",
            "quotes": results, "fresh": sum(1 for x in results.values() if x.get("fresh")),
            "open_regions": [r for r in ("US", "KR") if is_open(r, now)]}

LEVEL_RANK = {"normal": 0, "주의": 1, "경계": 2, "긴급": 3}

def _level(row: dict[str, Any]) -> tuple[int, str]:
    """Return severity and direction; uses only recent, regular-session bars."""
    if not (row.get("fresh") and row.get("open")):
        return 0, ""
    values = {k: _num(row.get(k)) for k in ("day_pct", "m5_pct", "m15_pct")}
    for direction, sign in (("하락", -1), ("상승", 1)):
        day = (values["day_pct"] or 0) * sign
        m5 = (values["m5_pct"] or 0) * sign
        m15 = (values["m15_pct"] or 0) * sign
        is_index = row.get("kind") == "index"
        if is_index:
            if day >= 4 or m5 >= 1.6 or m15 >= 3:
                return 3, direction
            if day >= 2.2 or m5 >= 0.8 or m15 >= 1.6:
                return 2, direction
            if day >= 1.2 or m5 >= 0.5:
                return 1, direction
        elif row.get("kind") in ("stock", "sector"):
            if day >= 8 or m5 >= 5 or m15 >= 7:
                return 3, direction
            if day >= 5 or m5 >= 3 or m15 >= 4:
                return 2, direction
            if day >= 3 or m5 >= 2:
                return 1, direction
    return 0, ""

def detect_events(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = snapshot.get("quotes") or {}
    events: list[dict[str, Any]] = []
    by_region = {"US": ["SPY", "QQQ"], "KR": ["^KS11", "^KQ11"]}
    covered = set()
    for region, tickers in by_region.items():
        assessed = [(t, *_level(rows[t])) for t in tickers if t in rows]
        for direction in ("하락", "상승"):
            positives = [(t, rank) for t, rank, d in assessed if d == direction and rank >= 1]
            if not positives:
                continue
            level = max(rank for _, rank in positives)
            if len(positives) >= 2:
                level = min(3, level + 1)
            if level >= 2:
                symbols = [t for t, _ in positives]
                covered.update(tickers)
                events.append({"key": f"{region}-{direction}", "region": region, "direction": direction,
                               "level": level, "symbols": symbols, "kind": "market"})
    for symbol, row in rows.items():
        if symbol in covered or row.get("kind") not in ("stock", "sector", "index"):
            continue
        rank, direction = _level(row)
        if rank >= 2:
            events.append({"key": f"{symbol}-{direction}", "region": row.get("region"), "direction": direction,
                           "level": rank, "symbols": [symbol], "kind": "single"})
    return sorted(events, key=lambda item: (-item["level"], item["key"]))[:6]

def format_alert(event: dict[str, Any], snapshot: dict[str, Any]) -> str:
    grade = {1: "주의", 2: "경계", 3: "긴급"}[event["level"]]
    emoji = "🚨" if event["level"] == 3 else "⚠️"
    area = "미국" if event["region"] == "US" else "한국"
    lines = [f"{emoji} [시장 {grade}] {area} 증시 {event['direction']} 이상징후",
             f"확인: {snapshot['checked_at']} / 출처: Yahoo 1분봉 (비공식·지연 가능)"]
    for symbol in event["symbols"][:4]:
        row = snapshot["quotes"][symbol]
        measures = ["5분", "15분", "전일비"]
        fields = ["m5_pct", "m15_pct", "day_pct"]
        changes = [f"{label} {row[field]:+.2f}%" for label, field in zip(measures, fields) if _num(row.get(field)) is not None]
        lines.append(f"• {row['name']}({symbol}): {row['price']:,.2f} | " + " / ".join(changes) + f" | 데이터 {row['age_sec']}초 전")
    lines.extend([
        "원인: 가격만으로는 확인 불가. 개별 뉴스·지표와 교차검증 필요.",
        "대응: 레버리지·신용 증거금과 포지션 집중도 점검. 급락 추격매도·무계획 물타기 금지.",
        "텔레그램에서 '봇 시장', '봇 시장 왜 떨어져?', '봇 시장 지금 팔아?'로 질문하세요.",
        "※ 자동 주문 없음 / 거래소 공식 실시간 시세 아님"
    ])
    return "\n".join(lines)

def _read_state() -> dict[str, Any]:
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}

def should_send(event: dict[str, Any], state: dict[str, Any], now: datetime) -> bool:
    previous = (state.get("sent") or {}).get(event["key"]) or {}
    if not previous:
        return True
    if event["level"] > int(previous.get("level") or 0):
        return True
    try:
        age = now - datetime.fromisoformat(previous["at"])
        cooldown = timedelta(minutes=45 if event["level"] == 3 else 120)
        return age >= cooldown
    except (KeyError, TypeError, ValueError):
        return True

def _chat_ids() -> list[str]:
    raw = (os.getenv("TELEGRAM_TARGET_CHAT_IDS") or "") + "," + (os.getenv("TELEGRAM_TARGET_CHAT_ID") or "")
    return list(dict.fromkeys(x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()))

def send_message(text: str) -> int:
    token = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip().strip("'\"")
    chats = _chat_ids()
    if not token or not chats:
        raise RuntimeError("Telegram secrets absent: TELEGRAM_BOT_TOKEN and TELEGRAM_TARGET_CHAT_ID(S)")
    sent = 0
    for chat in chats:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text[:3950], "disable_web_page_preview": True}, timeout=15)
        r.raise_for_status()
        if not r.json().get("ok"):
            raise RuntimeError("Telegram API rejected alert")
        sent += 1
    return sent

def run_once(send: bool = True, now: datetime | None = None, symbols: dict[str, tuple[str, str, str]] | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    data = scan(now, symbols=symbols)
    events = detect_events(data)
    state = _read_state()
    pending = [e for e in events if should_send(e, state, now)]
    result = {"checked_at": data["checked_at"], "fresh": data["fresh"], "open_regions": data["open_regions"],
              "detected": events, "pending": pending, "sent": 0}
    if send:
        # A silent monitor with a dead price source is not a functioning alarm.
        for region, core in (("US", ("SPY", "QQQ")), ("KR", ("^KS11", "^KQ11"))):
            if region not in data["open_regions"]:
                continue
            present = [data["quotes"].get(symbol) or {} for symbol in core]
            if any(x.get("fresh") for x in present):
                continue
            last_text = (state.get("feed_warnings") or {}).get(region, "")
            try:
                last = datetime.fromisoformat(last_text)
            except (TypeError, ValueError):
                last = now - timedelta(days=3)
            if now - last >= timedelta(hours=6):
                notice = (f"⚠️ [관제 데이터 장애] {region} 시장 시세가 확인되지 않습니다.\n"
                          f"시각 {data['checked_at']} KST. Yahoo 응답 지연/접속 차단/시장 휴장일 수 있습니다. "
                          "이 기간에는 급락 경보가 누락될 수 있으니 다른 실시간 시세를 확인하세요.")
                result["sent"] += send_message(notice)
                state.setdefault("feed_warnings", {})[region] = now.isoformat()
        for event in pending[:2]:
            message = format_alert(event, data)
            result["sent"] += send_message(message)
            state.setdefault("sent", {})[event["key"]] = {"at": now.isoformat(), "level": event["level"]}
            state["last_alert"] = {"at": now.isoformat(), "event": event, "message": message}
        if result["sent"]:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return result

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Read quotes but never send or persist.")
    parser.add_argument("--loop", action="store_true", help="Always-on worker, not intended for sleeping free hosting.")
    parser.add_argument("--interval", type=int, default=60)
    args = parser.parse_args()
    while True:
        try:
            run_once(send=not args.dry_run)
        except Exception as exc:
            print(f"[crisis-watch] FAILED {type(exc).__name__}: {exc}", flush=True)
            if not args.loop:
                raise
        if not args.loop:
            break
        time.sleep(max(30, args.interval))

if __name__ == "__main__":
    main()
