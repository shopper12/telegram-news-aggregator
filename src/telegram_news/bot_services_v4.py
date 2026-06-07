from __future__ import annotations

import re
from typing import Any

import requests

from . import bot_services_v3 as base

TIMEOUT = 2.0
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "text/html,*/*"}

EXTRA = {
    "삼성전자": "005930",
    "sk하이닉스": "000660",
    "하이닉스": "000660",
    "한미반도체": "042700",
    "두산에너빌리티": "034020",
    "현대차": "005380",
    "기아": "000270",
    "네이버": "035420",
    "카카오": "035720",
    "lg전자": "066570",
    "lg에너지솔루션": "373220",
    "셀트리온": "068270",
    "알테오젠": "196170",
    "파마리서치": "214450",
    "리가켐바이오": "141080",
    "에코프로": "086520",
    "에코프로비엠": "247540",
}


def _norm(x: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", str(x or "").lower())


def _clean(x: str) -> str:
    return re.sub(r"<[^>]+>", "", x).replace("\n", " ").replace("\t", " ").strip()


def _num(x: str) -> float | None:
    s = re.sub(r"[^0-9.-]", "", str(x or ""))
    try:
        return float(s) if s else None
    except Exception:
        return None


def _get(url: str, params: dict[str, str] | None = None):
    try:
        return requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    except Exception:
        return None


def _manual_code(q: str) -> str | None:
    m = {_norm(k): v for k, v in EXTRA.items()}
    return m.get(_norm(q))


def _search_code(q: str) -> str | None:
    if re.fullmatch(r"\d{6}", q.strip()):
        return q.strip()
    code = _manual_code(q)
    if code:
        return code
    for url, params in [
        ("https://finance.naver.com/search/searchList.naver", {"query": q}),
        ("https://search.naver.com/search.naver", {"query": q + " 주가"}),
    ]:
        r = _get(url, params)
        if not r or r.status_code != 200:
            continue
        r.encoding = "euc-kr"
        m = re.search(r"code=(\d{6})", r.text)
        if m:
            return m.group(1)
    return None


def _naver_rows(code: str) -> dict[str, Any] | None:
    rows = []
    for page in [1, 2]:
        r = _get("https://finance.naver.com/item/sise_day.naver", {"code": code, "page": str(page)})
        if not r or r.status_code != 200:
            continue
        r.encoding = "euc-kr"
        cells = [_clean(x) for x in re.findall(r"<span class=\"tah[^\"]*\">(.*?)</span>", r.text, re.S)]
        cells = [x for x in cells if x]
        i = 0
        while i + 6 < len(cells):
            if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", cells[i]):
                close = _num(cells[i + 1])
                high = _num(cells[i + 4])
                low = _num(cells[i + 5])
                vol = _num(cells[i + 6])
                if close and high and low and vol is not None:
                    rows.append((close, high, low, vol))
                i += 7
            else:
                i += 1
        if len(rows) >= 14:
            break
    if len(rows) < 2:
        return None
    rows.reverse()
    name = code
    r = _get("https://finance.naver.com/item/main.naver", {"code": code})
    if r and r.status_code == 200:
        r.encoding = "euc-kr"
        m = re.search(r"<title>\s*([^:<]+)", r.text)
        if m:
            name = _clean(m.group(1)) or code
    closes = [x[0] for x in rows]
    highs = [x[1] for x in rows]
    lows = [x[2] for x in rows]
    vols = [x[3] for x in rows]
    return {"name": name, "code": code, "closes": closes, "highs": highs, "lows": lows, "volumes": vols}


def _ma(a, n):
    return sum(a[-n:]) / n if len(a) >= n else None


def _rsi(a, n=14):
    if len(a) <= n:
        return None
    gains = []
    losses = []
    for i in range(-n, 0):
        d = a[i] - a[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    lg = sum(gains) / n
    ls = sum(losses) / n
    return 100 if ls == 0 else 100 - 100 / (1 + lg / ls)


def _fmt(x):
    if x is None:
        return "미확인"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:,.2f}"


def _simple_yahoo_quote(q: str) -> str:
    r = _get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{q.strip().upper()}",
        {"range": "1d", "interval": "1m"},
    )
    if not r or r.status_code != 200:
        return f"시세를 찾지 못했습니다: {q}"
    try:
        result = r.json().get("chart", {}).get("result", [])
        if not result:
            return f"시세를 찾지 못했습니다: {q}"
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        pct = (float(price) - float(prev)) / float(prev) * 100 if price and prev else None
        pct_text = "등락률 미확인" if pct is None else f"{pct:+.2f}%"
        return f"시세 {q.strip().upper()}\n{_fmt(float(price))} {meta.get('currency') or ''} ({pct_text})\n출처: Yahoo Finance"
    except Exception:
        return f"시세를 찾지 못했습니다: {q}"


def simple_quote(q: str) -> str:
    q = q.strip()
    if re.fullmatch(r"[A-Za-z.\-]{1,10}", q):
        return _simple_yahoo_quote(q)
    code = _search_code(q)
    if not code:
        return f"시세를 찾지 못했습니다: {q}"
    item = _naver_rows(code)
    if not item:
        return f"시세를 찾지 못했습니다: {q}"
    c = item["closes"]
    price = c[-1]
    prev = c[-2]
    pct = (price - prev) / prev * 100 if prev else None
    pct_text = "등락률 미확인" if pct is None else f"{pct:+.2f}%"
    return f"시세 {item['name']}({code})\n{_fmt(price)} KRW ({pct_text})\n출처: Naver Finance 일봉"


def fast_quote(q: str) -> str:
    if re.fullmatch(r"[A-Za-z.\-]{1,10}", q.strip()):
        return base.quote_text(q)
    code = _search_code(q)
    if not code:
        return f"시세를 찾지 못했습니다: {q}"
    item = _naver_rows(code)
    if not item:
        return f"시세를 찾지 못했습니다: {q}"
    c = item["closes"]
    h = item["highs"]
    l = item["lows"]
    price = c[-1]
    prev = c[-2]
    pct = (price - prev) / prev * 100 if prev else None
    ma5 = _ma(c, 5)
    ma20 = _ma(c, 20)
    rsi = _rsi(c, 14)
    support = min(l[-20:]) if len(l) >= 20 else min(l)
    resistance = max(h[-20:]) if len(h) >= 20 else max(h)
    score = 50
    if ma5 and price > ma5:
        score += 10
    if ma20 and price > ma20:
        score += 15
    if rsi is not None and 45 <= rsi <= 65:
        score += 10
    if rsi is not None and rsi > 75:
        score -= 15
    if price >= resistance * 0.995:
        score += 7
    if price <= support * 1.03:
        score += 5
    score = max(0, min(100, score))
    if score >= 75:
        call = "분할매수 후보"
        reason = "단기 추세와 모멘텀이 우세하다."
    elif score >= 60:
        call = "눌림대기/소액관찰"
        reason = "조건은 일부 좋지만 추격매수는 제한한다."
    elif score >= 45:
        call = "관망"
        reason = "방향성 우위가 약하다."
    else:
        call = "매수 보류"
        reason = "추세 점수가 낮아 방어가 우선이다."
    pct_text = "미확인" if pct is None else f"{pct:+.2f}%"
    rsi_text = "미확인" if rsi is None else f"{rsi:.1f}"
    return (
        f"금융퀀트 매매판단: {q}\n"
        f"{item['name']}({code}): {_fmt(price)} KRW ({pct_text})\n"
        f"기술점수: {score}/100 | RSI14 {rsi_text}\n"
        f"MA5/20: {_fmt(ma5)} / {_fmt(ma20)}\n"
        f"지지/저항: {_fmt(support)} / {_fmt(resistance)}\n"
        f"추천: {call}\n"
        f"전략: 손절 {_fmt(support)}, 1차목표 {_fmt(resistance)}\n"
        f"근거: {reason}\n"
        f"출처: Naver Finance 일봉. 실제 주문 전 증권사 현재가 재확인."
    )


def _target_from_trade(text: str) -> str | None:
    return base._extract_trade_target(text)


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    has_prefix, msg = base._strip_bot_prefix(message)
    if not has_prefix:
        return "명령어는 '봇'으로 시작해야 합니다. 예: 봇 뉴스"
    if msg.startswith("시세") or msg.lower().startswith("quote"):
        target = re.sub(r"^(시세|quote)\s*", "", msg, flags=re.IGNORECASE).strip()
        return simple_quote(target)
    target = _target_from_trade(msg)
    if target:
        return fast_quote(target)
    return base.handle_command(user_id=user_id, message=message, latest_report=latest_report)
