from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import requests
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .report_cache import load_latest_report

app = FastAPI(title="Telegram News Aggregator Bot API")
API_VERSION = "news-public-message-v8"


class RefreshRequest(BaseModel):
    hours: int = 1
    limit: int = 999
    briefing_kind: str = "regular"


def _require_api_key(x_api_key: str | None) -> None:
    expected = os.getenv("NEWS_BOT_API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(status_code=401, detail="invalid_api_key")


def _report_data() -> dict:
    return load_latest_report()


def _report_text() -> str:
    data = _report_data()
    return str(data.get("report") or "최신 뉴스 리포트가 없습니다.")


def _payload_from_data(data: dict) -> dict:
    message = str(data.get("report") or "뉴스 없음").strip() or "뉴스 없음"
    return {
        "ok": bool(data.get("ok", False)),
        "message": message,
        "kind": data.get("kind"),
        "hours": data.get("hours"),
        "source": data.get("source"),
        "generated_at": data.get("generated_at"),
        "fallback_reason": data.get("fallback_reason"),
        "version": API_VERSION,
    }


def _bot_message_payload() -> dict:
    return _payload_from_data(_report_data())


def _refresh_latest_report(*, hours: int = 1, limit: int = 999, briefing_kind: str = "regular") -> dict:
    env = os.environ.copy()
    env["BRIEFING_KIND"] = briefing_kind
    cmd = [
        sys.executable,
        "scripts/run_once.py",
        "run",
        "--hours",
        str(hours),
        "--limit",
        str(limit),
    ]
    try:
        completed = subprocess.run(cmd, cwd=Path.cwd(), env=env, text=True, capture_output=True, timeout=900)
        if completed.returncode != 0:
            logging.error(
                f"[refresh_failed] returncode={completed.returncode}\n"
                f"STDOUT: {completed.stdout[-2000:]}\n"
                f"STDERR: {completed.stderr[-2000:]}"
            )
            existing = _report_data()
            existing["refresh_error"] = completed.stderr[-500:]
            existing["refresh_failed"] = True
            return existing
    except subprocess.TimeoutExpired:
        logging.error("[refresh_failed] timeout after 900s")
        existing = _report_data()
        existing["refresh_error"] = "timeout"
        existing["refresh_failed"] = True
        return existing
    except Exception as e:
        logging.error(f"[refresh_failed] exception: {e}")
        existing = _report_data()
        existing["refresh_error"] = str(e)
        existing["refresh_failed"] = True
        return existing
    return _report_data()


def _command_body(text: str) -> str:
    value = str(text or "").strip()
    if value == "봇":
        return "도움말"
    for prefix in ["봇 ", "봇:", "봇아 "]:
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def _is_refresh_command(text: str) -> bool:
    compact = _command_body(text).replace(" ", "").lower()
    return compact in {"뉴스갱신", "뉴스새로고침", "새로고침", "뉴스업데이트", "refresh", "뉴스refresh"}


def _is_news_command(text: str) -> bool:
    compact = _command_body(text).replace(" ", "").lower()
    return compact in {"뉴스", "/뉴스", "!뉴스", "news", "/news", "시황", "브리핑"}


def _is_help_command(text: str) -> bool:
    compact = _command_body(text).replace(" ", "").lower()
    return compact in {"도움", "도움말", "help", "/help", "?"}


def _is_quote_command(text: str) -> bool:
    body = _command_body(text).strip().lower()
    return body.startswith("시세") or body.startswith("quote")


def _quote_target(text: str) -> str:
    body = _command_body(text).strip()
    return re.sub(r"^(시세|quote)\s*", "", body, flags=re.IGNORECASE).strip()


def _help_text() -> str:
    return (
        "명령어 안내\n"
        "봇 뉴스 - 저장된 최신 뉴스/시황\n"
        "봇 뉴스갱신 - 스케줄/API에서 새로 수집, 카카오 앱에서는 저장 리포트 즉시 표시\n"
        "봇 시세 삼성전자 / 봇 시세 005930 / 봇 시세 NVDA\n"
        "봇 도움말 - 명령어 안내"
    )


def _refreshed_message() -> str:
    data = _refresh_latest_report(hours=1, limit=999, briefing_kind="regular")
    return str(data.get("report") or "뉴스 없음").strip() or "뉴스 없음"


_QUOTE_SYMBOLS: dict[str, str] = {
    "삼성전자": "005930.KS",
    "삼성": "005930.KS",
    "005930": "005930.KS",
    "sk하이닉스": "000660.KS",
    "하이닉스": "000660.KS",
    "000660": "000660.KS",
    "현대차": "005380.KS",
    "기아": "000270.KS",
    "네이버": "035420.KS",
    "naver": "035420.KS",
    "카카오": "035720.KS",
    "lg전자": "066570.KS",
    "한미반도체": "042700.KS",
    "두산에너빌리티": "034020.KS",
    "에코프로": "086520.KQ",
    "에코프로비엠": "247540.KQ",
}


def _normalize_quote_key(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", value.strip().lower())


def _quote_symbol(target: str) -> str:
    raw = target.strip()
    key = _normalize_quote_key(raw)
    if key in _QUOTE_SYMBOLS:
        return _QUOTE_SYMBOLS[key]
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 6:
        return f"{digits}.KS"
    return raw.upper()


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "미확인"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _fast_quote_text(target: str) -> str:
    q = target.strip()
    if not q:
        return "시세 대상을 입력하세요. 예: 봇 시세 삼성전자"

    symbol = _quote_symbol(q)
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "5d", "interval": "1d"}
    try:
        res = requests.get(url, params=params, timeout=2.2)
        if res.status_code != 200:
            return f"시세 조회 실패: {q}\nYahoo 응답 HTTP {res.status_code}. 한국 KOSDAQ 종목이면 종목코드로 다시 시도하세요."
        result = (res.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return f"시세를 찾지 못했습니다: {q}\n예: 봇 시세 삼성전자 / 봇 시세 005930 / 봇 시세 NVDA"
        meta = result.get("meta") or {}
        price = meta.get("regularMarketPrice")
        prev = meta.get("previousClose")
        currency = meta.get("currency") or ""
        exch = meta.get("exchangeName") or "Yahoo"
        name = meta.get("shortName") or symbol
        pct = None
        if price is not None and prev:
            pct = ((float(price) / float(prev)) - 1) * 100
        pct_text = "미확인" if pct is None else f"{pct:+.2f}%"
        return (
            f"빠른 시세: {q}\n"
            f"{name} ({symbol})\n"
            f"현재/최근가: {_fmt_price(float(price) if price is not None else None)} {currency} ({pct_text})\n"
            f"전일종가: {_fmt_price(float(prev) if prev is not None else None)} {currency}\n"
            f"소스: {exch}/Yahoo 지연 데이터\n"
            f"주의: 메신저R 5초 제한 때문에 빠른 조회만 제공합니다. 주문 전 증권사 현재가를 재확인하세요."
        )
    except requests.Timeout:
        return f"시세 조회 지연: {q}\n외부 시세 서버가 2초 안에 응답하지 않았습니다. 잠시 뒤 다시 시도하세요."
    except Exception as exc:
        logging.warning("fast quote failed: %s", exc)
        return f"시세 조회 실패: {q}\n원인: 외부 시세 서버 응답 오류. 예: 봇 시세 삼성전자 / 봇 시세 NVDA"


@app.get("/")
def root() -> dict:
    return {
        "ok": True,
        "service": "telegram_news_bot_api",
        "version": API_VERSION,
        "endpoints": [
            "/health",
            "/api/news",
            "/api/news.txt",
            "/api/news-message",
            "/api/refresh",
            "/api/status",
            "/api/kakao-skill",
            "/skill",
            "/reply",
            "/api/reply",
            "/docs",
        ],
    }


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "telegram_news_bot_api", "version": API_VERSION}


@app.get("/api/status")
def get_status() -> dict:
    from datetime import datetime
    data = _report_data()
    generated_at = data.get("generated_at", "")
    stale_seconds = None
    if generated_at:
        try:
            stale_seconds = int(
                (datetime.now() - datetime.fromisoformat(generated_at)).total_seconds()
            )
        except Exception:
            pass
    return {
        "ok": data.get("ok", False),
        "generated_at": generated_at,
        "stale_seconds": stale_seconds,
        "stale_hours": round(stale_seconds / 3600, 1) if stale_seconds else None,
        "kind": data.get("kind"),
        "source": data.get("source"),
        "report_length": len(str(data.get("report", ""))),
        "has_report": bool(str(data.get("report", "")).strip()),
        "version": API_VERSION,
    }


@app.get("/api/news")
def get_news() -> dict:
    return _report_data()


@app.get("/api/news-message")
def get_news_message() -> dict:
    return _bot_message_payload()


@app.get("/api/news.txt", response_class=PlainTextResponse)
def get_news_text() -> str:
    return _report_text()


@app.post("/api/refresh")
def refresh_news(req: RefreshRequest, x_api_key: str | None = Header(default=None)) -> dict:
    _require_api_key(x_api_key)
    return _refresh_latest_report(hours=req.hours, limit=req.limit, briefing_kind=req.briefing_kind)


def _extract_utterance(payload: dict) -> str:
    return str(
        payload.get("userRequest", {}).get("utterance")
        or payload.get("utterance")
        or payload.get("action", {}).get("params", {}).get("utterance")
        or payload.get("message")
        or payload.get("msg")
        or payload.get("text")
        or ""
    ).strip()


def _extract_user_id(payload: dict) -> str:
    user = payload.get("userRequest", {}).get("user") or {}
    props = user.get("properties") or {}
    for key in ["plusfriendUserKey", "appUserId", "botUserKey"]:
        value = props.get(key) or user.get(key)
        if value:
            return str(value)
    return str(payload.get("user_id") or "kakao-default")


def _kakao_simple_text(text: str) -> dict:
    value = str(text or "뉴스 없음").strip() or "뉴스 없음"
    return {
        "version": "2.0",
        "template": {
            "outputs": [
                {
                    "simpleText": {
                        "text": value[:990]
                    }
                }
            ]
        },
    }


def _skill_answer(utterance: str, user_id: str = "kakao-default") -> str:
    text = str(utterance or "").strip()
    if not text:
        text = "봇 도움말"
    if not text.startswith("봇"):
        text = "봇 " + text

    if _is_help_command(text):
        return _help_text()

    if _is_refresh_command(text):
        cached = _report_text()
        return ("카카오 앱 5초 제한 때문에 즉시 갱신은 실행하지 않습니다. 저장된 최신 리포트를 표시합니다.\n\n" + cached)[:990]

    if _is_news_command(text):
        return _report_text()[:990]

    if _is_quote_command(text):
        return _fast_quote_text(_quote_target(text))[:990]

    try:
        from .bot_services_private import handle_command
    except Exception as e:
        logging.warning(f"bot_services_private import failed: {e}")
        from .bot_services_v5 import handle_command
    latest = _report_text()
    return str(handle_command(user_id=user_id, message=text, latest_report=latest))[:990]


async def _payload_from_request(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        pass
    try:
        form = await request.form()
        return dict(form)
    except Exception:
        pass
    try:
        body = (await request.body()).decode("utf-8", errors="ignore").strip()
        if body:
            return {"message": body}
    except Exception:
        pass
    return {}


async def _handle_kakao_skill(request: Request) -> dict:
    payload = await _payload_from_request(request)
    utterance = _extract_utterance(payload)
    user_id = _extract_user_id(payload)
    return _kakao_simple_text(_skill_answer(utterance, user_id))


def _query_message(request: Request) -> str:
    params = request.query_params
    return str(
        params.get("message")
        or params.get("msg")
        or params.get("text")
        or params.get("utterance")
        or params.get("q")
        or "봇 도움말"
    )


@app.get("/api/kakao-skill")
def kakao_skill_get() -> dict:
    return _kakao_simple_text("카카오 스킬 서버 정상. 공식 카카오 스킬은 POST /skill, 카카오 봇 앱은 GET /reply?message=봇%20뉴스 를 쓰세요.")


@app.post("/api/kakao-skill")
async def kakao_skill(request: Request) -> dict:
    return await _handle_kakao_skill(request)


@app.get("/skill")
def skill_get() -> dict:
    return _kakao_simple_text("카카오 스킬 서버 정상. 공식 카카오 스킬은 POST /skill, 카카오 봇 앱은 GET /reply?message=봇%20뉴스 를 쓰세요.")


@app.post("/skill")
async def skill(request: Request) -> dict:
    return await _handle_kakao_skill(request)


@app.get("/reply", response_class=PlainTextResponse)
def reply_get(request: Request) -> str:
    return _skill_answer(_query_message(request), "plain-get")


@app.get("/api/reply", response_class=PlainTextResponse)
def api_reply_get(request: Request) -> str:
    return _skill_answer(_query_message(request), "plain-get")


@app.post("/reply", response_class=PlainTextResponse)
async def reply_post(request: Request) -> str:
    payload = await _payload_from_request(request)
    return _skill_answer(_extract_utterance(payload), _extract_user_id(payload))


@app.post("/api/reply", response_class=PlainTextResponse)
async def api_reply_post(request: Request) -> str:
    payload = await _payload_from_request(request)
    return _skill_answer(_extract_utterance(payload), _extract_user_id(payload))
