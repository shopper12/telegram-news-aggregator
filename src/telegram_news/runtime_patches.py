from __future__ import annotations

import re
from typing import Any


def _body(api_server: Any, text: str) -> str:
    return api_server._command_body(str(text or "")).strip()


def _is_reading(api_server: Any, text: str) -> bool:
    b = _body(api_server, text)
    return b.startswith("사주") or b.startswith("운세")


def _question(api_server: Any, text: str) -> str:
    return re.sub(r"^(사주|운세)\s*", "", _body(api_server, text)).strip()


def _category(question: str) -> str:
    if any(w in question for w in ["돈", "재물", "투자", "주식", "매매"]):
        return "money"
    if any(w in question for w in ["연애", "결혼", "관계", "상대"]):
        return "relationship"
    if any(w in question for w in ["직장", "일", "시험", "공부", "이직"]):
        return "work"
    return "general"


def _varied_reading(api_server: Any, user_id: str, text: str) -> str:
    from . import bot_services as base
    from .advice_variants import pick_variant

    profile = base.get_profile(user_id)
    if not profile:
        return "먼저 생년월일을 등록하세요. 예: 봇 생년월일 YYYY-MM-DD HH:MM 여"

    q = _question(api_server, text)
    key = f"{profile.birth_date}|{profile.birth_time}|{profile.gender}|{profile.calendar}|{q}"
    seed = api_server._stable_seed(key) if hasattr(api_server, "_stable_seed") else abs(hash(key))
    pressure_pool = ["비겁", "식상", "재성", "관성", "인성", "비겁-재성", "식상-관성", "재성-관성"]
    useful_pool = ["목", "화", "토", "금", "수", "목·화", "화·토", "금·수"]
    flow_pool = [
        "초반 정리 후 중반부터 실행력이 붙는 흐름",
        "관계 조율을 먼저 해야 일이 풀리는 흐름",
        "체력과 현금성 자원을 아끼면서 재진입하는 흐름",
        "공식 절차와 문서화가 유리한 흐름",
        "작게 반복해 신뢰를 회복하는 흐름",
        "기준이 명확할수록 속도가 빨라지는 흐름",
        "혼자 밀기보다 역할 분담이 성패를 가르는 흐름",
        "기존 방식 정리 후 새 구조로 바꿔야 하는 흐름",
    ]
    category = _category(q)
    action, caution = pick_variant(key, category)

    if category == "money":
        focus = "재물: 감정적 확신보다 기준·비중·회수 계획이 먼저다. 큰 결정보다 반복 가능한 원칙이 유리하다."
    elif category == "relationship":
        focus = "관계: 말보다 반복 행동, 책임 분담, 생활 리듬 일치가 핵심 판단 기준이다."
    elif category == "work":
        focus = "일/학업: 체면보다 산출물과 마감 단위가 중요하다. 평가받는 구조에 들어가야 성과가 난다."
    else:
        focus = "종합: 방향보다 구조를 먼저 잡아야 한다. 감정 판단을 줄이고 검증 가능한 기준으로 선택해야 한다."

    return (
        "전문가식 사주 리딩\n"
        "비공개 프로필 기준으로 해석함\n"
        f"핵심 축: {pressure_pool[(seed // 7) % len(pressure_pool)]} 이슈가 강하게 작동하는 흐름\n"
        f"보완 기운: {useful_pool[(seed // 11) % len(useful_pool)]} 성향을 생활·일·관계에서 의식적으로 보강\n"
        f"운의 흐름: {flow_pool[(seed // 13) % len(flow_pool)]}\n"
        f"{focus}\n"
        f"실행 조언: {action}\n"
        f"이번 주 점검: {caution}\n"
        "주의: 채팅에는 생년월일을 재표시하지 않는다. 정확한 명식은 절기·출생지·음양력 검증 후 별도 계산 필요."
    )


def apply(api_server: Any) -> None:
    api_server.API_VERSION = "news-public-message-v10"
    original_skill_answer = api_server._skill_answer

    def patched_skill_answer(utterance: str, user_id: str = "kakao-default") -> str:
        if _is_reading(api_server, utterance):
            return _varied_reading(api_server, user_id, utterance)[:990]
        return original_skill_answer(utterance, user_id)

    def patched_reply_get(request):
        user_id = str(request.query_params.get("user_id") or request.query_params.get("sender") or "plain-get")
        return patched_skill_answer(api_server._query_message(request), user_id)

    api_server._skill_answer = patched_skill_answer
    for route in getattr(api_server.app, "routes", []):
        if getattr(route, "path", "") in {"/reply", "/api/reply"} and "GET" in getattr(route, "methods", set()):
            route.endpoint = patched_reply_get
            if hasattr(route, "dependant"):
                route.dependant.call = patched_reply_get
