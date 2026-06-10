from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import json
import os
import re

import requests
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse

from . import bot_services as base
from .report_cache import load_latest_report

app = FastAPI(title="Telegram News Messenger API")
API_VERSION = "messenger-stable-v1"
base.PROFILE_PATH = Path(os.getenv("BOT_PROFILE_PATH", "data/bot_profiles.json"))


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _strip_bot(text: str) -> str:
    text = _clean(text)
    if text == "봇":
        return "도움말"
    for prefix in ["봇 ", "봇:", "봇아 "]:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    return text


def _hash(key: str) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)


def _pick(pool: list[str], seed: int, step: int) -> str:
    return pool[(seed // step) % len(pool)]


def _help() -> str:
    return (
        "명령어 안내\n"
        "봇 뉴스 - 저장된 최신 뉴스/시황\n"
        "봇 시세 삼성전자 / 봇 시세 005930 / 봇 시세 NVDA\n"
        "봇 생년월일 YYYY-MM-DD HH:MM 성별 - 사주 프로필 저장\n"
        "봇 사주 [질문] - 프로필 기반 리딩\n"
        "봇 도움말 - 명령어 안내"
    )


def _news() -> str:
    return str(load_latest_report().get("report") or "최신 뉴스 리포트가 없습니다.")[:1200]


def _save_birth(user_id: str, body: str) -> str:
    birth = base.parse_birth_command(body)
    if not birth:
        return "생년월일 형식이 맞지 않습니다. 예: 봇 생년월일 1987-12-28 08:30 여"
    base.save_profile(user_id, *birth)
    return "프로필 저장 완료\n이후 '봇 사주 질문'으로 조회하세요."


def _saju(user_id: str, body: str) -> str:
    profile = base.get_profile(user_id)
    if not profile:
        return "먼저 생년월일을 등록하세요. 예: 봇 생년월일 1987-12-28 08:30 여"
    question = re.sub(r"^(사주|운세)\s*", "", body).strip()
    key = f"{profile.birth_date}|{profile.birth_time}|{profile.gender}|{profile.calendar}|{question}"
    seed = _hash(key)

    if any(w in question for w in ["돈", "재물", "투자", "주식", "매매"]):
        category = "money"
        focus = "재물: 감정적 확신보다 기준·비중·회수 계획이 먼저다."
    elif any(w in question for w in ["연애", "결혼", "관계", "상대"]):
        category = "relationship"
        focus = "관계: 말보다 반복 행동, 책임 분담, 생활 리듬 일치가 판단 기준이다."
    elif any(w in question for w in ["직장", "일", "시험", "공부", "이직"]):
        category = "work"
        focus = "일/학업: 체면보다 산출물과 마감 단위가 중요하다."
    else:
        category = "general"
        focus = "종합: 방향보다 구조를 먼저 잡고 검증 가능한 기준으로 선택해야 한다."

    actions = {
        "money": [
            "현재 선택의 기준을 3줄로 적고 넘지 말아야 할 한계를 먼저 정하라.",
            "후보를 늘리지 말고 유지할 것 1개와 정리할 것 1개를 분리하라.",
            "바로 움직이지 말고 10분 대기 후 같은 판단인지 확인하라.",
            "관심 대상을 3개 이하로 줄이고 이유가 약한 선택은 보류하라.",
        ],
        "relationship": [
            "설득보다 관찰을 우선하고 말보다 반복 행동을 기준으로 보라.",
            "요구사항을 1개로 줄이고 실제로 바뀔 행동을 날짜와 함께 정하라.",
            "감정 확인보다 시간·역할·미래계획 중 하나를 구체적으로 합의하라.",
            "상대 반응을 시험하지 말고 지킬 수 있는 경계선 1개를 말하라.",
        ],
        "work": [
            "자료수집을 멈추고 제출 가능한 초안 1개를 만드는 쪽으로 몰아라.",
            "투입시간보다 산출물 개수로 평가하고 40분 단위 결과물을 남겨라.",
            "설명할 쟁점을 3줄로 줄이고 근거자료는 뒤에 붙여라.",
            "완벽한 설계보다 마감 가능한 버전을 먼저 내라.",
        ],
        "general": [
            "오늘 할 일은 3개가 아니라 1개다. 완료 기준이 숫자로 보이는 것만 선택하라.",
            "새 결심을 추가하지 말고 효과 없는 루틴 1개를 끊어라.",
            "감정이 강한 선택은 하루 유예하고 비용·시간·회복가능성을 적어라.",
            "인정받는 목표보다 실제로 삶을 개선하는 행동 1개를 먼저 처리하라.",
        ],
    }
    cautions = [
        "확신이 강할수록 반대 근거 1개를 먼저 확인해야 한다.",
        "피로한 날의 결정은 과잉통제와 회피가 섞이기 쉽다.",
        "남의 속도와 비교하면 판단이 급해진다.",
        "말로 정리된 계획보다 캘린더에 들어간 행동만 믿어라.",
    ]
    pressure_pool = ["비겁", "식상", "재성", "관성", "인성", "비겁-재성", "식상-관성", "재성-관성"]
    useful_pool = ["목", "화", "토", "금", "수", "목·화", "화·토", "금·수"]
    flow_pool = [
        "초반 정리 후 중반부터 실행력이 붙는 흐름",
        "관계 조율을 먼저 해야 일이 풀리는 흐름",
        "체력과 현금성 자원을 아끼면서 재진입하는 흐름",
        "공식 절차와 문서화가 유리한 흐름",
        "작게 반복해 신뢰를 회복하는 흐름",
        "기준이 명확할수록 속도가 빨라지는 흐름",
    ]

    return (
        "전문가식 사주 리딩\n"
        "비공개 프로필 기준으로 해석함\n"
        f"핵심 축: {_pick(pressure_pool, seed, 7)} 이슈가 강하게 작동하는 흐름\n"
        f"보완 기운: {_pick(useful_pool, seed, 11)} 성향을 생활·일·관계에서 보강\n"
        f"운의 흐름: {_pick(flow_pool, seed, 13)}\n"
        f"{focus}\n"
        f"실행 조언: {actions[category][(seed // 17) % len(actions[category])]}\n"
        f"이번 주 점검: {cautions[(seed // 19) % len(cautions)]}\n"
        "주의: 정확한 명식은 절기·출생지·음양력 검증 후 별도 계산 필요."
    )


_SYMBOLS = {
    "삼성전자": "005930.KS", "삼성": "005930.KS", "005930": "005930.KS",
    "하이닉스": "000660.KS", "sk하이닉스": "000660.KS", "000660": "000660.KS",
    "현대차": "005380.KS", "기아": "000270.KS", "네이버": "035420.KS", "카카오": "035720.KS",
    "한미반도체": "042700.KS", "두산에너빌리티": "034020.KS",
}


def _quote_symbol(target: str) -> str:
    t = re.sub(r"[\s·().,㈜주식회사_-]+", "", target.strip().lower())
    if t in _SYMBOLS:
        return _SYMBOLS[t]
    digits = re.sub(r"\D", "", target)
    if len(digits) == 6:
        return f"{digits}.KS"
    return target.strip().upper()


def _quote(body: str) -> str:
    target = re.sub(r"^(시세|quote)\s*", "", body, flags=re.IGNORECASE).strip()
    if not target:
        return "시세 대상을 입력하세요. 예: 봇 시세 삼성전자"
    symbol = _quote_symbol(target)
    try:
        res = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "5d", "interval": "1d"},
            timeout=2.5,
        )
        if res.status_code != 200:
            return f"시세 조회 실패: {target}\n종목코드 또는 영문 티커로 다시 시도하세요."
        result = (res.json().get("chart", {}).get("result") or [None])[0]
        if not result:
            return f"시세를 찾지 못했습니다: {target}"
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
            "주의: Yahoo 지연 데이터입니다. 주문 전 증권사 현재가를 재확인하세요."
        )
    except Exception:
        return f"시세 조회 지연: {target}\n외부 시세 서버가 응답하지 않습니다. 잠시 뒤 다시 시도하세요."


def answer(message: str, user_id: str) -> str:
    body = _strip_bot(message)
    low = body.replace(" ", "").lower()
    if low in {"도움", "도움말", "help", "/help", "?"}:
        return _help()
    if low in {"뉴스", "/뉴스", "!뉴스", "news", "/news", "시황", "브리핑"}:
        return _news()
    if low in {"뉴스갱신", "뉴스새로고침", "새로고침", "뉴스업데이트", "refresh", "뉴스refresh"}:
        return "메신저R에서는 즉시 갱신을 실행하지 않습니다. 저장된 최신 리포트를 표시합니다.\n\n" + _news()
    if any(k in body for k in ["생년월일", "생일", "출생", "사주등록", "프로필"]):
        return _save_birth(user_id, body)
    if body.startswith("사주") or body.startswith("운세"):
        return _saju(user_id, body)
    if body.startswith("시세") or body.lower().startswith("quote"):
        return _quote(body)
    return "명령어를 인식하지 못했습니다. '봇 도움말'을 입력하세요."


async def _payload(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        pass
    try:
        return dict(await request.form())
    except Exception:
        pass
    try:
        raw = (await request.body()).decode("utf-8", errors="ignore").strip()
        return {"message": raw} if raw else {}
    except Exception:
        return {}


def _query_message(request: Request) -> str:
    q = request.query_params
    return _clean(q.get("message") or q.get("msg") or q.get("text") or q.get("utterance") or q.get("q") or "봇 도움말")


def _query_user(request: Request) -> str:
    q = request.query_params
    return _clean(q.get("sender") or q.get("user_id") or q.get("room") or "default-user")


def _kakao(text: str) -> dict:
    return {"version": "2.0", "template": {"outputs": [{"simpleText": {"text": text[:990]}}]}}


@app.get("/health")
def health() -> dict:
    return {"ok": True, "service": "telegram_news_bot_api", "version": API_VERSION}


@app.get("/")
def root() -> dict:
    return {"ok": True, "version": API_VERSION, "endpoints": ["/health", "/reply", "/api/reply", "/skill"]}


@app.get("/reply", response_class=PlainTextResponse)
def reply_get(request: Request) -> str:
    return answer(_query_message(request), _query_user(request))[:1400]


@app.get("/api/reply", response_class=PlainTextResponse)
def api_reply_get(request: Request) -> str:
    return answer(_query_message(request), _query_user(request))[:1400]


@app.post("/reply", response_class=PlainTextResponse)
async def reply_post(request: Request) -> str:
    data = await _payload(request)
    return answer(_clean(data.get("message") or data.get("msg") or data.get("text") or data.get("utterance")), _clean(data.get("sender") or data.get("user_id") or "default-user"))[:1400]


@app.post("/api/reply", response_class=PlainTextResponse)
async def api_reply_post(request: Request) -> str:
    return await reply_post(request)


@app.get("/skill")
def skill_get() -> dict:
    return _kakao("카카오/메신저 서버 정상. 메신저R은 /reply?message=봇%20뉴스&sender=사용자 를 쓰세요.")


@app.post("/skill")
async def skill_post(request: Request) -> dict:
    data = await _payload(request)
    msg = _clean(data.get("userRequest", {}).get("utterance") if isinstance(data.get("userRequest"), dict) else "") or _clean(data.get("message") or data.get("utterance") or data.get("text"))
    user = "kakao-user"
    return _kakao(answer(msg, user))


@app.get("/api/kakao-skill")
def kakao_get() -> dict:
    return skill_get()


@app.post("/api/kakao-skill")
async def kakao_post(request: Request) -> dict:
    return await skill_post(request)
