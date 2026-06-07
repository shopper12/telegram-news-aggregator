from __future__ import annotations

import re
from typing import Any

import requests

from . import bot_services as base

QUOTE_TIMEOUT = base.QUOTE_TIMEOUT
_KR_NAME_CACHE: dict[str, str] | None = None


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", value.strip().lower())


def _load_kr_names() -> dict[str, str]:
    global _KR_NAME_CACHE
    if _KR_NAME_CACHE is not None:
        return _KR_NAME_CACHE
    names: dict[str, str] = {}
    try:
        from pykrx import stock

        for market in ["KOSPI", "KOSDAQ", "KONEX"]:
            try:
                for code in stock.get_market_ticker_list(market=market):
                    name = stock.get_market_ticker_name(code)
                    if name:
                        names[_normalize_name(name)] = code
            except Exception:
                continue
    except Exception:
        names = {}
    for name, code in base.KR_NAME_TO_CODE.items():
        names[_normalize_name(name)] = code
    _KR_NAME_CACHE = names
    return names


def _kr_code_matches(query: str, limit: int = 5) -> list[str]:
    key = _normalize_name(query)
    if not key:
        return []
    names = _load_kr_names()
    exact = [code for name, code in names.items() if name == key]
    if exact:
        return exact[:limit]
    starts = [code for name, code in names.items() if name.startswith(key)]
    if starts:
        return starts[:limit]
    contains = [code for name, code in names.items() if key in name]
    return contains[:limit]


def _yahoo_chart(symbol: str) -> dict[str, Any] | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "1d", "interval": "1m"},
            timeout=QUOTE_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        if price is None:
            return None
        pct = None
        if prev:
            pct = (float(price) - float(prev)) / float(prev) * 100
        return {
            "symbol": symbol,
            "price": float(price),
            "pct": pct,
            "currency": meta.get("currency") or "",
            "exchange": meta.get("exchangeName") or meta.get("exchangeTimezoneName") or "",
        }
    except Exception:
        return None


def quote_text(query: str) -> str:
    q = query.strip()
    candidates: list[str] = []
    lower = q.lower()
    if lower in base.KR_NAME_TO_CODE:
        code = base.KR_NAME_TO_CODE[lower]
        candidates = [f"{code}.KS", f"{code}.KQ"]
    elif q in base.KR_NAME_TO_CODE:
        code = base.KR_NAME_TO_CODE[q]
        candidates = [f"{code}.KS", f"{code}.KQ"]
    elif re.fullmatch(r"\d{6}", q):
        candidates = [f"{q}.KS", f"{q}.KQ"]
    elif re.fullmatch(r"[A-Za-z.\-]{1,10}", q):
        candidates = [q.upper()]
    else:
        for code in _kr_code_matches(q):
            candidates.extend([f"{code}.KS", f"{code}.KQ"])

    if not candidates:
        return "시세 형식: 시세 삼성전자 / 시세 한미반도체 / 시세 005930 / 시세 NVDA"

    seen = set()
    for symbol in candidates:
        if symbol in seen:
            continue
        seen.add(symbol)
        item = _yahoo_chart(symbol)
        if not item:
            continue
        price = item["price"]
        price_text = f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"
        pct_text = "등락률 미확인" if item["pct"] is None else f"{item['pct']:+.2f}%"
        return f"시세 {q}\n{item['symbol']}: {price_text} {item['currency']} ({pct_text})\n거래소: {item['exchange']}\n출처: Yahoo Finance chart API"
    return f"시세를 찾지 못했습니다: {q}"


def _private_saju(user_id: str, msg: str) -> str:
    profile = base.get_profile(user_id)
    if not profile:
        return "먼저 생년월일을 등록하세요. 예: 생년월일 YYYY-MM-DD HH:MM 여"
    question = re.sub(r"^(사주|운세)\s*", "", msg).strip()
    raw = base.saju_reading(profile, question)
    raw = re.sub(r"생년월일:.*\n", "비공개 프로필 사용 중\n", raw)
    raw = raw.replace("생년월일은 채팅 답변에 다시 표시하지 않습니다. ", "")
    return raw


def help_text() -> str:
    return (
        "명령어\n"
        "뉴스 - 최신 중요 뉴스/시황\n"
        "시세 삼성전자 / 시세 한미반도체 / 시세 005930 / 시세 NVDA\n"
        "생년월일 YYYY-MM-DD HH:MM 성별 - 사주 프로필 비공개 저장\n"
        "사주 [질문] - 저장된 비공개 생년월일 기반 간이 분석\n"
        "타로 [질문] - 3카드 리딩\n"
        "도움말 - 명령어 안내"
    )


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    msg = message.strip()
    birth = base.parse_birth_command(msg)
    if birth:
        base.save_profile(user_id, *birth)
        return "프로필 저장 완료\n생년월일은 채팅 답변에 다시 표시하지 않습니다. 이후 '사주 질문'으로 조회하세요."

    q = msg.lower()
    if q in {"도움", "도움말", "help", "/help", "?"}:
        return help_text()
    if q in {"뉴스", "/뉴스", "!뉴스", "news", "/news", "시황", "브리핑"}:
        return latest_report or "뉴스 없음"
    if msg.startswith("시세") or msg.lower().startswith("quote"):
        target = re.sub(r"^(시세|quote)\s*", "", msg, flags=re.IGNORECASE).strip()
        return quote_text(target)
    if msg.startswith("사주") or msg.startswith("운세"):
        return _private_saju(user_id, msg)
    if msg.startswith("타로"):
        question = re.sub(r"^타로\s*", "", msg).strip()
        return base.tarot_reading(user_id, question)
    return "명령어를 인식하지 못했습니다. '도움말'을 입력하세요."
