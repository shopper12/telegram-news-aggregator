from __future__ import annotations

from typing import Any
import hashlib
import re

from . import bot_services as base
from .report_cache import load_latest_report


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _body(messenger_api: Any, message: str) -> str:
    return messenger_api._strip_bot(message).strip()


def _hash(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest(), 16)


def _pick(pool: list[str], seed: int, shift: int) -> str:
    return pool[(seed // shift) % len(pool)]


def telegram_news_text() -> str:
    try:
        data = load_latest_report()
        generated = data.get("generated_at") or "시간미상"
        kind = data.get("kind") or "regular"
        source = data.get("source") or "unknown"
        fallback = data.get("fallback_reason")
        report = str(data.get("report") or "최신 텔레그램 뉴스 리포트가 없습니다.").strip()
        head = f"📰 텔레그램 채널 뉴스 종합\n생성: {generated} | kind={kind} | source={source}"
        if fallback:
            head += f" | fallback={fallback}"
        return (head + "\n\n" + report)[:1800]
    except Exception as exc:
        return f"텔레그램 뉴스 리포트 읽기 실패: {type(exc).__name__}: {exc}\nActions의 Telegram news briefing이 성공해 reports/latest_report.json을 갱신해야 합니다."


def _question(body: str) -> str:
    return re.sub(r"^(사주|운세)\s*", "", body).strip()


def _category(question: str) -> str:
    if any(w in question for w in ["돈", "재물", "투자", "주식", "매매", "수익"]):
        return "money"
    if any(w in question for w in ["연애", "결혼", "관계", "상대", "남자", "여자"]):
        return "relationship"
    if any(w in question for w in ["직장", "일", "시험", "공부", "이직", "승진", "커리어"]):
        return "work"
    if any(w in question for w in ["건강", "체력", "피로", "수면"]):
        return "health"
    return "general"


def advanced_saju_text(user_id: str, body: str) -> str:
    try:
        profile = base.get_profile(user_id)
    except Exception as exc:
        return f"프로필 읽기 실패: {type(exc).__name__}: {exc}"
    if not profile:
        return "먼저 생년월일을 등록하세요. 예: 봇 생년월일 1987-12-28 08:30 여"

    question = _question(body)
    key = f"{profile.birth_date}|{profile.birth_time}|{profile.gender}|{profile.calendar}|{question}"
    seed = _hash(key)
    category = _category(question)

    axis_pool = [
        "비겁-재성 충돌: 독립성과 손익 판단이 동시에 강해져, 통제욕과 손실회피가 같이 올라온다",
        "식상-관성 긴장: 표현·실행 욕구는 큰데 평가·규칙을 의식해 속도가 끊긴다",
        "인성-식상 과다: 생각과 해석은 깊지만 산출물 전환이 늦어질 수 있다",
        "재성-관성 압박: 돈·성과·책임을 한꺼번에 짊어지는 흐름이라 기준 없는 확장이 위험하다",
        "관성-인성 구조: 제도권·문서·자격·공식 루트에서 힘을 받지만, 완벽주의가 발목을 잡는다",
        "비겁-식상 활성: 자기방식으로 밀고 나가고 싶어지나, 협업 비용을 과소평가하기 쉽다",
    ]
    balance_pool = [
        "목: 방향 설정과 시작력",
        "화: 노출·표현·설득력",
        "토: 루틴·소화·중재력",
        "금: 기준·절제·손절력",
        "수: 정보수집·학습·회복력",
        "목·화: 아이디어를 밖으로 꺼내는 힘",
        "금·수: 기준을 세운 뒤 검증하는 힘",
        "토·금: 복잡한 일을 절차화하는 힘",
    ]
    risk_pool = [
        "감정이 강해질수록 판단 근거를 더 많이 찾는 척하지만 실제로는 결론을 이미 정해둘 수 있다",
        "피로가 누적되면 선택지가 늘어나는 것 자체가 스트레스가 되어, 극단적 결론으로 도망가기 쉽다",
        "남과 비교하는 순간 속도가 빨라지지만, 그 속도는 실행력이 아니라 불안 반응일 수 있다",
        "관계나 돈에서 확답을 빨리 얻으려 할수록 상대와 시장의 변동성을 과소평가한다",
        "완벽한 조건이 올 때까지 미루다가, 막판에 과격하게 실행하는 패턴을 조심해야 한다",
    ]
    timing_pool = [
        "초반 2일은 정리·삭제, 중반 3일은 실행, 후반 2일은 검증과 회수에 써라",
        "이번 주는 새 판을 벌이기보다 기존 선택의 누수를 막는 쪽이 유리하다",
        "처음에는 답답해도 문서화·수치화한 결정이 뒤로 갈수록 힘을 받는다",
        "사람을 설득하기보다 구조를 바꾸는 쪽이 효과가 크다",
        "하루 단위 결정보다 4주 반복 패턴을 보고 판단해야 오판이 줄어든다",
    ]

    category_focus = {
        "money": "재물/투자: 지금 핵심은 운이 좋으냐보다 손실 제한 구조가 있느냐이다. 수익 상상보다 비중·손절·재진입 조건이 먼저다.",
        "relationship": "관계: 상대의 말보다 반복 행동과 생활 리듬을 보라. 감정 확인을 많이 할수록 본질이 흐려진다.",
        "work": "일/커리어: 능력 자체보다 산출물·마감·평가 구조가 성패를 가른다. 혼자 오래 고민하면 장점이 약점으로 바뀐다.",
        "health": "건강/체력: 의지 문제가 아니라 회복 자원 관리 문제로 봐야 한다. 수면·식사·자극량을 먼저 고정해야 판단도 안정된다.",
        "general": "종합: 지금은 큰 결론보다 판단 구조를 세우는 시기다. 감정과 사실, 욕망과 비용을 분리해야 한다.",
    }
    execution = {
        "money": [
            "오늘은 보유/관심 종목을 3개로 줄이고, 각 종목마다 진입가·무효가·최대손실액을 숫자로 적어라.",
            "추가매수는 금지하고, 이미 손실난 포지션의 회복 시나리오와 폐기 시나리오를 둘 다 써라.",
            "한 번의 큰 판단보다 3회 분할 판단이 맞다. 매수보다 현금비중 회복을 먼저 점검하라.",
        ],
        "relationship": [
            "상대에게 요구할 것은 1개만 남겨라. 대신 그 1개는 날짜와 행동으로 확인 가능해야 한다.",
            "오늘은 결론 대화를 하지 말고, 반복되는 불만을 돈·시간·역할·미래계획 중 하나로 번역하라.",
            "상대 반응을 시험하지 말고 네가 지킬 경계선을 먼저 정하라.",
        ],
        "work": [
            "자료수집을 멈추고 제출 가능한 초안 1개를 만들어라. 완성도보다 피드백 가능한 형태가 중요하다.",
            "오늘 가장 어려운 업무를 오전 첫 블록에 배치하고, 오후에는 반복·정리 업무만 남겨라.",
            "쟁점을 3줄로 압축하고 근거는 뒤에 붙여라. 설명 구조가 곧 실력으로 보이는 운이다.",
        ],
        "health": [
            "이번 주는 운동 강도보다 수면·수분·식사 시간을 고정하라. 몸의 변동성을 낮추는 게 우선이다.",
            "카페인·야식·수면시각 중 하나만 줄여라. 한 번에 다 바꾸면 다시 무너진다.",
            "증상이 반복되면 기록을 남겨라. 요일·시간·식사·수면을 같이 적어야 원인이 보인다.",
        ],
        "general": [
            "오늘 할 일은 1개만 잡아라. 완료 기준이 숫자나 파일로 남는 것만 선택하라.",
            "새 결심을 추가하지 말고, 효과 없는 루틴 1개를 끊는 것이 먼저다.",
            "감정이 강한 선택은 하루 유예하고 비용·시간·회복가능성을 표로 적어라.",
        ],
    }

    axis = _pick(axis_pool, seed, 7)
    balance = _pick(balance_pool, seed, 11)
    risk = _pick(risk_pool, seed, 13)
    timing = _pick(timing_pool, seed, 17)
    action = _pick(execution[category], seed, 19)

    return (
        "전문가식 심층 사주 리딩\n"
        "※ 등록 프로필 기반 간이 명리 프레임. 정확한 만세력 산출 전에는 방향성 판단으로만 사용.\n"
        f"1) 핵심 구조: {axis}\n"
        f"2) 보완 기운: {balance}을 생활에서 의식적으로 보강해야 균형이 잡힌다.\n"
        f"3) 질문 초점: {category_focus[category]}\n"
        f"4) 이번 주 운용법: {timing}\n"
        f"5) 리스크: {risk}.\n"
        f"6) 실행 처방: {action}\n"
        "판정: 운을 맞히는 용도가 아니라, 반복 패턴을 끊고 선택 기준을 세우는 데 써라."
    )


def apply(messenger_api: Any) -> None:
    original_answer = messenger_api.answer

    def patched_answer(message: str, user_id: str) -> str:
        body = _body(messenger_api, message)
        low = body.replace(" ", "").lower()
        if low in {"뉴스", "/뉴스", "!뉴스", "news", "/news", "시황", "브리핑", "뉴스갱신", "뉴스새로고침", "새로고침", "뉴스업데이트", "refresh", "뉴스refresh"}:
            return telegram_news_text()
        if body.startswith("사주") or body.startswith("운세"):
            return advanced_saju_text(user_id, body)
        return original_answer(message, user_id)

    messenger_api.answer = patched_answer
    messenger_api.API_VERSION = "messenger-stable-v6"
