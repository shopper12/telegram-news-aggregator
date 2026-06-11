from __future__ import annotations

from typing import Any
import re

import requests

_SYMBOLS = {
    "삼성전자": "005930.KS",
    "삼성": "005930.KS",
    "하이닉스": "000660.KS",
    "sk하이닉스": "000660.KS",
    "현대차": "005380.KS",
    "기아": "000270.KS",
    "네이버": "035420.KS",
    "카카오": "035720.KS",
    "한미반도체": "042700.KS",
    "두산에너빌리티": "034020.KS",
    "소룩스": "290690.KQ",
    "국보디자인": "066620.KQ",
    "파워넷": "037030.KQ",
    "성호전자": "043260.KQ",
    "비나텍": "126340.KQ",
    "삼지전자": "037460.KQ",
    "아모텍": "052710.KQ",
    "대한전선": "001440.KS",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", _clean(value).lower())


def _has_hangul(value: str) -> bool:
    return bool(re.search(r"[가-힣]", value or ""))


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _naver_search(target: str) -> tuple[str, str] | None:
    digits = re.sub(r"\D", "", target)
    if len(digits) == 6:
        return digits, target
    try:
        res = requests.get(
            "https://m.stock.naver.com/api/search/all",
            params={"keyword": target},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=3.5,
        )
        if res.status_code != 200:
            return None
        wanted = _norm(target)
        first = None
        for obj in _walk(res.json()):
            code = _clean(obj.get("itemCode") or obj.get("stockCode") or obj.get("code") or obj.get("symbolCode"))
            name = _clean(obj.get("stockName") or obj.get("itemName") or obj.get("name") or obj.get("korName"))
            if not re.fullmatch(r"\d{6}", code):
                continue
            if first is None:
                first = (code, name or target)
            if name and _norm(name) == wanted:
                return code, name
        return first
    except Exception:
        return None


def _quote_naver(target: str) -> str | None:
    resolved = _naver_search(target)
    if not resolved:
        return None
    code, name = resolved
    try:
        res = requests.get(
            f"https://m.stock.naver.com/api/stock/{code}/basic",
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=3.5,
        )
        if res.status_code != 200:
            return None
        data = res.json()
        stock_name = _clean(data.get("stockName") or data.get("itemName") or name or target)
        close = _clean(data.get("closePrice") or data.get("nowVal") or data.get("price"))
        diff = _clean(data.get("compareToPreviousClosePrice") or data.get("diff"))
        ratio = _clean(data.get("fluctuationsRatio") or data.get("rate"))
        volume = _clean(data.get("accumulatedTradingVolume") or data.get("volume"))
        ratio_text = f"{ratio}%" if ratio and not ratio.endswith("%") else (ratio or "등락률 미확인")
        return (
            f"빠른 시세: {stock_name}({code})\n"
            f"현재/최근가: {close or '미확인'} KRW ({ratio_text})\n"
            f"전일대비: {diff or '미확인'}\n"
            f"거래량: {volume or '미확인'}\n"
            "소스: Naver Finance 지연 데이터\n"
            "주의: 주문 전 증권사 현재가를 재확인하세요."
        )
    except Exception:
        return None


def _yahoo_symbol(target: str) -> str:
    key = _norm(target)
    if key in _SYMBOLS:
        return _SYMBOLS[key]
    digits = re.sub(r"\D", "", target)
    if len(digits) == 6:
        return f"{digits}.KS"
    return target.strip().upper()


def _quote_yahoo(target: str) -> str | None:
    symbol = _yahoo_symbol(target)
    try:
        res = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=3.5,
        )
        if res.status_code != 200:
            return None
        result = (res.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return None
        meta = result.get("meta") or {}
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose")
        pct = "미확인"
        if price is not None and prev:
            pct = f"{((float(price) / float(prev)) - 1) * 100:+.2f}%"
        return (
            f"빠른 시세: {target}\n"
            f"{meta.get('shortName') or symbol} ({symbol})\n"
            f"현재/최근가: {price if price is not None else '미확인'} {meta.get('currency') or ''} ({pct})\n"
            f"전일종가: {prev if prev is not None else '미확인'}\n"
            "소스: Yahoo 지연 데이터\n"
            "주의: 주문 전 증권사 현재가를 재확인하세요."
        )
    except Exception:
        return None


def quote_text(target: str) -> str:
    target = _clean(target)
    if not target:
        return "시세 대상을 입력하세요. 예: 봇 시세 삼성전자"
    if _has_hangul(target) or re.fullmatch(r"\d{6}", re.sub(r"\D", "", target)):
        result = _quote_naver(target) or _quote_yahoo(target)
    else:
        result = _quote_yahoo(target)
    if result:
        return result
    return (
        f"시세를 찾지 못했습니다: {target}\n"
        "Naver Finance와 Yahoo를 순서대로 조회했지만 실패했습니다.\n"
        "예: 봇 시세 삼성전자 / 봇 시세 한미반도체 / 봇 시세 국보디자인 / 봇 시세 005930 / 봇 시세 NVDA"
    )


def apply(messenger_api: Any) -> None:
    original_answer = messenger_api.answer

    def patched_answer(message: str, user_id: str) -> str:
        body = messenger_api._strip_bot(message)
        if body.startswith("시세") or body.lower().startswith("quote"):
            target = re.sub(r"^(시세|quote)\s*", "", body, flags=re.IGNORECASE).strip()
            return quote_text(target)
        return original_answer(message, user_id)

    messenger_api.answer = patched_answer
    messenger_api.API_VERSION = "messenger-stable-v4"
