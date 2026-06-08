from __future__ import annotations

import re

from . import bot_services_v5 as v5
from . import bot_services_v4 as v4
from . import saju_engine

US_SYMBOL_ALIASES = {
    "애플": "AAPL", "마이크로소프트": "MSFT", "마소": "MSFT", "엔비디아": "NVDA", "테슬라": "TSLA",
    "아마존": "AMZN", "알파벳": "GOOGL", "구글": "GOOGL", "메타": "META", "넷플릭스": "NFLX",
    "브로드컴": "AVGO", "팔란티어": "PLTR", "코스트코": "COST", "월마트": "WMT", "JP모건": "JPM",
    "제이피모건": "JPM", "버크셔": "BRK-B", "코카콜라": "KO", "펩시": "PEP", "맥도날드": "MCD",
    "나이키": "NKE", "디즈니": "DIS", "보잉": "BA", "록히드마틴": "LMT", "엑슨모빌": "XOM",
    "셰브론": "CVX", "코인베이스": "COIN", "마이크로스트래티지": "MSTR", "로블록스": "RBLX", "우버": "UBER",
    "슈퍼마이크로": "SMCI", "슈마컴": "SMCI", "퀄컴": "QCOM", "TSMC": "TSM", "아이온큐": "IONQ",
    "리게티": "RGTI", "소파이": "SOFI", "로빈후드": "HOOD", "SPY": "SPY", "QQQ": "QQQ",
    "TQQQ": "TQQQ", "SOXL": "SOXL", "EWY": "EWY",
}

SECTOR_BY_ELEMENT = {
    "목": "성장주·바이오 흐름 주목",
    "화": "에너지·반도체 섹터 활성",
    "토": "안정형 자산·배당주 유리",
    "금": "금속·IT하드웨어 섹터 관심",
    "수": "금융·유동성 관련 이슈 주목",
}


def _alias_target(target: str) -> str:
    raw = str(target or "").strip()
    compact = re.sub(r"[\s·().,_-]+", "", raw).upper()
    for key, symbol in US_SYMBOL_ALIASES.items():
        if compact == re.sub(r"[\s·().,_-]+", "", key).upper():
            return symbol
    return raw


def _relation(my_el: str, other_el: str) -> tuple[str, str]:
    if my_el == other_el:
        return "비겁", "서로 닮은 에너지. 공감대는 높지만 경쟁 구도로 바뀔 수 있다."
    if saju_engine.GENERATES.get(my_el) == other_el:
        return "상생", "내가 상대에게 에너지를 주는 관계. 먼저 소진될 수 있어 경계 설정이 필요하다."
    if saju_engine.GENERATES.get(other_el) == my_el:
        return "상생", "상대가 나를 돕는 관계. 협력·지원받는 구조에서 시너지가 난다."
    if saju_engine.CONTROLS.get(my_el) == other_el:
        return "상극", "내가 상대를 통제하거나 이끄는 관계. 장기적으로 상대 반발을 주의해야 한다."
    if saju_engine.CONTROLS.get(other_el) == my_el:
        return "상극", "상대가 나를 자극하는 관계. 긴장감이 성장을 만들 수 있지만 소모도 크다."
    return "중립", "직접 상생·상극보다 전체 명식의 균형을 봐야 한다."


def _profile(user_id: str):
    return v4.base._get_profile(user_id or "default")


def _today_fortune(profile) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    y, m, d, hour = saju_engine.profile_parts(profile)
    my_chart = saju_engine.chart_from_ymdh(y, m, d, hour)
    my_el = saju_engine.ELEMENT[my_chart["day"][0]]
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    today_chart = saju_engine.chart_from_ymdh(now.year, now.month, now.day, now.hour)
    today_el = saju_engine.ELEMENT[today_chart["day"][0]]
    rel, text = _relation(my_el, today_el)
    if rel == "상생" and saju_engine.GENERATES.get(today_el) == my_el:
        flow = "오늘은 지원·학습·계획에 유리. 도움을 받는 구조가 낫다."
    elif rel == "상생":
        flow = "오늘은 흐름이 협조적. 새 시도는 가능하지만 체력 소모를 본다."
    elif rel == "상극" and saju_engine.CONTROLS.get(my_el) == today_el:
        flow = "오늘은 내가 주도하는 날. 결정·추진에 유리하지만 과속은 줄인다."
    elif rel == "상극":
        flow = "오늘은 마찰 가능성. 중요 결정은 한 번 늦춰 확인한다."
    elif rel == "비겁":
        flow = "오늘은 경쟁·협력 모두 강해지는 날. 같은 편과 역할을 나눠야 한다."
    else:
        flow = text
    return (
        f"오늘의 운세 [{now:%m/%d}]\n━━━━━━━━\n"
        f"오늘 일진: {today_chart['day']}\n"
        f"흐름: {flow}\n"
        f"투자 포인트: {SECTOR_BY_ELEMENT.get(today_el, '시장 수급 확인 우선')}\n"
        f"한마디: 즉흥 반응보다 기준을 먼저 세운다.\n"
        f"주의: 간이 계산. 절기·출생지 미반영."
    )[:750]


def _compatibility(profile, target_date: str) -> str:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", target_date)
    if not m:
        return "형식: 봇 궁합 YYYY-MM-DD\n예: 봇 궁합 1990-03-15"
    y, mo, d = map(int, m.groups())
    my_y, my_m, my_d, my_h = saju_engine.profile_parts(profile)
    my_chart = saju_engine.chart_from_ymdh(my_y, my_m, my_d, my_h)
    other_chart = saju_engine.chart_from_ymdh(y, mo, d, 12)
    my_stem = my_chart["day"][0]
    other_stem = other_chart["day"][0]
    my_el = saju_engine.ELEMENT[my_stem]
    other_el = saju_engine.ELEMENT[other_stem]
    rel, text = _relation(my_el, other_el)
    partnership = "파트너십: 역할을 나누고 의사결정 기준을 문서화하면 소모를 줄인다."
    if rel == "상생":
        partnership = "파트너십: 지원·보완 구조가 가능하나 한쪽의 과부담을 막아야 한다."
    elif rel == "상극":
        partnership = "파트너십: 긴장감은 성과를 만들 수 있으나 돈·권한 기준을 먼저 합의해야 한다."
    elif rel == "비겁":
        partnership = "파트너십: 판단 속도가 비슷해도 수익 배분·책임 분담을 명확히 해야 한다."
    return (
        "오행 궁합 분석\n━━━━━━━━\n"
        f"나의 일간: {my_stem}({my_el})\n"
        f"상대 일간: {other_stem}({other_el})\n"
        f"관계 유형: {rel}\n"
        f"해석: {text}\n"
        f"{partnership}\n"
        "주의: 오행 간이 분석. 실제 궁합은 전체 명식 기준."
    )[:750]


def help_text() -> str:
    return (
        "명령어는 반드시 '봇'으로 시작\n"
        "봇 뉴스 - 최신 주식 뉴스 브리핑\n"
        "봇 뉴스갱신 / 봇 새로고침 - 최근 1시간 뉴스 즉시 갱신\n"
        "봇 시세 [종목] - 가격만\n"
        "봇 차트 [종목] - 시세 + 차트패턴 요약\n"
        "봇 [이름] 생년월일 1987-12-28 08:30 여 - 이름별 프로필 저장\n"
        "봇 [이름] 사주 [질문] - 실계산 기반 사주 리딩\n"
        "봇 [이름] 점성술 [질문] - 점성술\n"
        "봇 타로 [질문] - 전문가식 3카드 리딩\n"
        "봇 오늘운세 - 오늘 일진 기반 하루 운세\n"
        "봇 궁합 YYYY-MM-DD - 오행 궁합 분석\n"
        "봇 도움말 - 이 메시지"
    )


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    has_prefix, msg = v4.base._strip_bot_prefix(message)
    if not has_prefix:
        return "명령어는 '봇'으로 시작해야 합니다. 예: 봇 뉴스"

    q = msg.strip().lower()
    if q in {"도움", "도움말", "help", "/help", "?"}:
        return help_text()

    if q in {"오늘운세", "오늘 운세", "일운", "오늘"}:
        profile = _profile(user_id)
        if not profile:
            return "먼저 생년월일을 등록하세요. 예: 봇 생년월일 1987-12-28 08:30 여"
        return _today_fortune(profile)

    if msg.startswith("궁합"):
        profile = _profile(user_id)
        if not profile:
            return "먼저 생년월일을 등록하세요."
        return _compatibility(profile, msg.replace("궁합", "", 1).strip())

    if msg.startswith("시세") or msg.lower().startswith("quote"):
        target = re.sub(r"^(시세|quote)\s*", "", msg, flags=re.IGNORECASE).strip()
        return v4.simple_quote(_alias_target(target))

    if msg.startswith("차트"):
        target = re.sub(r"^차트\s*", "", msg).strip()
        return v4.fast_quote(_alias_target(target))

    trade_target = v4.base._extract_trade_target(msg)
    if trade_target:
        return v4.fast_quote(_alias_target(trade_target))

    return v5.handle_command(user_id=user_id, message=message, latest_report=latest_report)
