from __future__ import annotations

from typing import Any
import re

import requests

CODE_MAP = {
    "삼성전자": "005930",
    "삼성": "005930",
    "하이닉스": "000660",
    "sk하이닉스": "000660",
    "현대차": "005380",
    "기아": "000270",
    "네이버": "035420",
    "카카오": "035720",
    "한미반도체": "042700",
    "두산에너빌리티": "034020",
    "소룩스": "290690",
    "국보디자인": "066620",
    "파워넷": "037030",
    "성호전자": "043260",
    "비나텍": "126340",
    "삼지전자": "037460",
    "아모텍": "052710",
    "대한전선": "001440",
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", _clean(value).lower())


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://m.stock.naver.com/",
    }


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _resolve_code(target: str) -> tuple[str, str] | None:
    target = _clean(target)
    digits = re.sub(r"\D", "", target)
    if len(digits) == 6:
        return digits, target
    mapped = CODE_MAP.get(_norm(target))
    if mapped:
        return mapped, target
    for endpoint in ["https://m.stock.naver.com/api/search/all", "https://m.stock.naver.com/api/search/autoComplete"]:
        try:
            res = requests.get(endpoint, params={"keyword": target}, headers=_headers(), timeout=4.0)
            if res.status_code != 200:
                continue
            first = None
            wanted = _norm(target)
            for obj in _walk(res.json()):
                code = _clean(obj.get("itemCode") or obj.get("stockCode") or obj.get("code") or obj.get("symbolCode"))
                name = _clean(obj.get("stockName") or obj.get("itemName") or obj.get("name") or obj.get("korName"))
                if not re.fullmatch(r"\d{6}", code):
                    continue
                if first is None:
                    first = (code, name or target)
                if name and _norm(name) == wanted:
                    return code, name
            if first:
                return first
        except Exception:
            continue
    return None


def naver_quote_text(target: str) -> str:
    target = _clean(target)
    if not target:
        return "시세 대상을 입력하세요. 예: 봇 시세 삼성전자"
    resolved = _resolve_code(target)
    if not resolved:
        return f"네이버 금융에서 종목을 찾지 못했습니다: {target}\n예: 봇 시세 삼성전자 / 봇 시세 국보디자인 / 봇 시세 005930"
    code, fallback_name = resolved
    try:
        res = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic", headers=_headers(), timeout=4.0)
        if res.status_code != 200:
            return f"네이버 금융 시세 조회 실패: {target}\nHTTP {res.status_code} / code={code}"
        data = res.json()
        name = _clean(data.get("stockName") or data.get("itemName") or fallback_name)
        price = _clean(data.get("closePrice") or data.get("nowVal") or data.get("price"))
        diff = _clean(data.get("compareToPreviousClosePrice") or data.get("diff"))
        ratio = _clean(data.get("fluctuationsRatio") or data.get("rate"))
        volume = _clean(data.get("accumulatedTradingVolume") or data.get("volume"))
        ratio_text = f"{ratio}%" if ratio and not ratio.endswith("%") else (ratio or "등락률 미확인")
        return (
            f"빠른 시세: {name}({code})\n"
            f"현재/최근가: {price or '미확인'} KRW ({ratio_text})\n"
            f"전일대비: {diff or '미확인'}\n"
            f"거래량: {volume or '미확인'}\n"
            "소스: 네이버 금융 지연 데이터\n"
            "주의: 주문 전 증권사 현재가를 재확인하세요."
        )
    except Exception as exc:
        return f"네이버 금융 시세 조회 실패: {target}\n원인: {type(exc).__name__}: {exc}"


def apply(messenger_api: Any) -> None:
    original_answer = messenger_api.answer

    def patched_answer(message: str, user_id: str) -> str:
        body = messenger_api._strip_bot(message)
        if body.startswith("시세") or body.lower().startswith("quote"):
            target = re.sub(r"^(시세|quote)\s*", "", body, flags=re.IGNORECASE).strip()
            return naver_quote_text(target)
        return original_answer(message, user_id)

    messenger_api.answer = patched_answer
    messenger_api.API_VERSION = "messenger-stable-v5"
