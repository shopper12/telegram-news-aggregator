from __future__ import annotations

import re

from . import bot_services_v4 as v4
from . import saju_engine


def _norm(text: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", str(text or "").lower())


def _key(name: str) -> str:
    return "name:" + _norm(name)


def _birth(msg: str):
    text = str(msg or "").strip()
    patterns = [
        r"^(?P<n>[가-힣A-Za-z0-9_]{1,20})\s*(?:생년월일|생일|출생|사주등록|프로필)\s*(?P<r>.+)$",
        r"^(?:생년월일|생일|출생|사주등록|프로필)\s*(?P<n>[가-힣A-Za-z0-9_]{1,20})\s*(?P<r>.+)$",
    ]
    for pattern in patterns:
        m = re.match(pattern, text)
        if not m:
            continue
        data = v4.base.base.parse_birth_command("생년월일 " + m.group("r"))
        if data:
            return m.group("n"), data
    return None


def _named(msg: str, words: str):
    text = str(msg or "").strip()
    patterns = [
        rf"^(?P<n>[가-힣A-Za-z0-9_]{{1,20}})\s*(?:{words})(?P<q>.*)$",
        rf"^(?:{words})\s*(?P<n>[가-힣A-Za-z0-9_]{{1,20}})(?P<q>.*)$",
    ]
    for pattern in patterns:
        m = re.match(pattern, text)
        if m:
            return m.group("n"), (m.group("q") or "").strip()
    return None


def _profile(name: str):
    return v4.base._get_profile(_key(name))


def _saju(name: str, question: str) -> str:
    p = _profile(name)
    if not p:
        return f"{name} 프로필이 없습니다. 예: 봇 {name} 생년월일 1987-12-28 08:30 여"
    return saju_engine.reading(name, p, question)


def _astro(name: str, question: str) -> str:
    p = _profile(name)
    if not p:
        return f"{name} 프로필이 없습니다. 예: 봇 {name} 생년월일 1987-12-28 08:30 여"
    return saju_engine.astrology(name, p, question)


def help_text() -> str:
    return (
        "명령어는 반드시 '봇'으로 시작\n"
        "봇 뉴스 - 최근 1시간 중요 뉴스\n"
        "봇 시세 삼성전자 / 봇 시세 NVDA - 가격만\n"
        "봇 삼성전자 살까 / 봇 NVDA 어때 - 매매판단\n"
        "봇 지니 생년월일 1987-12-28 08:30 여 - 이름별 프로필 저장\n"
        "봇 지니 사주 / 봇 지니 오늘의 사주 / 봇 지니 사주 돈운 - 명식·만세력 포함\n"
        "봇 지니 점성술 / 봇 지니 별자리 연애 - 점성술\n"
        "봇 타로 [질문] - 3카드 리딩"
    )


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    has_prefix, msg = v4.base._strip_bot_prefix(message)
    if not has_prefix:
        return "명령어는 '봇'으로 시작해야 합니다. 예: 봇 뉴스"

    b = _birth(msg)
    if b:
        name, data = b
        v4.base._save_profile(_key(name), *data)
        return f"{name} 프로필 저장 완료\n다음부터 '봇 {name} 사주' 또는 '봇 {name} 점성술'로 조회하세요."

    q = msg.lower().strip()
    if q in {"도움", "도움말", "help", "/help", "?"}:
        return help_text()

    a = _named(msg, "점성술|별자리|서양점성술")
    if a:
        return _astro(a[0], a[1])

    s = _named(msg, "오늘의\s*사주|오늘\s*사주|사주|운세")
    if s:
        question = s[1]
        if "오늘" in msg and "오늘" not in question:
            question = "오늘 " + question
        return _saju(s[0], question)

    if msg.startswith("점성술") or msg.startswith("별자리") or msg.startswith("서양점성술"):
        p = v4.base._get_profile(user_id or "default")
        question = re.sub(r"^(점성술|별자리|서양점성술)\s*", "", msg).strip()
        return saju_engine.astrology("기본프로필", p, question) if p else "먼저 생년월일을 등록하세요. 예: 봇 생년월일 1987-12-28 08:30 여"

    if msg.startswith("오늘의 사주") or msg.startswith("오늘 사주") or msg.startswith("사주") or msg.startswith("운세"):
        p = v4.base._get_profile(user_id or "default")
        question = re.sub(r"^(오늘의\s*사주|오늘\s*사주|사주|운세)\s*", "", msg).strip()
        if "오늘" in msg and "오늘" not in question:
            question = "오늘 " + question
        return saju_engine.reading("기본프로필", p, question) if p else "먼저 생년월일을 등록하세요. 예: 봇 생년월일 1987-12-28 08:30 여"

    return v4.handle_command(user_id=user_id, message=message, latest_report=latest_report)
