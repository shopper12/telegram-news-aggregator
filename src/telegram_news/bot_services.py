from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
import base64
import hashlib
import json
import os
import random
import re
from typing import Any

import requests

PROFILE_PATH = Path(os.getenv("BOT_PROFILE_PATH", "data/bot_profiles.json"))
QUOTE_TIMEOUT = float(os.getenv("QUOTE_TIMEOUT_SECONDS", "4"))

PROFILE_GITHUB_REPO = os.getenv("PROFILE_GITHUB_REPO", "shopper12/telegram-news-aggregator")
PROFILE_GITHUB_PATH = os.getenv("PROFILE_GITHUB_PATH", "data/bot_profiles.json")

KR_NAME_TO_CODE = {
    "삼성전자": "005930", "sk하이닉스": "000660", "하이닉스": "000660",
    "현대차": "005380", "기아": "000270", "네이버": "035420",
    "naver": "035420", "카카오": "035720", "lg전자": "066570",
    "lg에너지솔루션": "373220", "셀트리온": "068270", "두산에너빌리티": "034020",
    "한미반도체": "042700", "에코프로": "086520", "에코프로비엠": "247540",
}

TAROT_MAJOR = [
    ("마법사", "도구는 이미 있으나, 실행 순서를 정해야 한다."),
    ("여사제", "보이는 정보보다 숨은 조건을 먼저 확인해야 한다."),
    ("여황제", "자원과 관계망이 늘어나는 흐름이다."),
    ("황제", "규칙, 책임, 구조화가 성패를 가른다."),
    ("교황", "전문가 조언과 기존 제도를 활용하는 쪽이 안전하다."),
    ("연인", "선택의 문제다. 감정보다 기준을 먼저 세워야 한다."),
    ("전차", "방향을 정하면 빠르지만, 과속 리스크가 있다."),
    ("힘", "강압보다 지속력이 유리하다."),
    ("은둔자", "속도를 늦추고 근거를 재검토해야 한다."),
    ("운명의 수레바퀴", "변동성이 크다. 타이밍 관리가 핵심이다."),
    ("정의", "계약, 규칙, 숫자 검증이 중요하다."),
    ("매달린 사람", "지금은 관점 전환과 대기 비용을 계산할 때다."),
    ("죽음", "기존 방식을 끝내고 새 구조로 넘어가는 신호다."),
    ("절제", "혼합, 균형, 분할 전략이 맞다."),
    ("악마", "집착과 손실회피가 판단을 흐릴 수 있다."),
    ("탑", "예상 밖 충격에 대비해야 한다."),
    ("별", "회복 가능성은 있으나 시간이 필요하다."),
    ("달", "정보가 불완전하다. 추정 매매나 확신을 피해야 한다."),
    ("태양", "가시성과 자신감이 올라오는 흐름이다."),
    ("심판", "과거 결과를 평가하고 결정을 내려야 한다."),
    ("세계", "한 사이클이 마무리되고 다음 단계로 넘어간다."),
]


@dataclass
class UserProfile:
    user_id: str
    birth_date: str
    birth_time: str = ""
    gender: str = ""
    calendar: str = "solar"
    created_at: str = ""
    updated_at: str = ""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_profiles_local() -> dict[str, dict[str, Any]]:
    if not PROFILE_PATH.exists():
        return {}
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_profiles_local(data: dict[str, dict[str, Any]]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _gh_token() -> str | None:
    return os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")


def _load_profiles() -> dict[str, dict[str, Any]]:
    token = _gh_token()
    if not token:
        return _load_profiles_local()
    url = f"https://api.github.com/repos/{PROFILE_GITHUB_REPO}/contents/{PROFILE_GITHUB_PATH}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        content = base64.b64decode(r.json()["content"]).decode("utf-8")
        data = json.loads(content)
        return data if isinstance(data, dict) else {}
    except Exception:
        return _load_profiles_local()


def _save_profiles(data: dict[str, dict[str, Any]]) -> None:
    token = _gh_token()
    if not token:
        _save_profiles_local(data)
        return
    url = f"https://api.github.com/repos/{PROFILE_GITHUB_REPO}/contents/{PROFILE_GITHUB_PATH}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github.v3+json"}
    encoded = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")).decode("utf-8")
    sha = None
    try:
        r = requests.get(url, headers=headers, timeout=5)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass
    payload: dict[str, Any] = {"message": "chore: update bot profiles", "content": encoded, "branch": "main"}
    if sha:
        payload["sha"] = sha
    try:
        requests.put(url, json=payload, headers=headers, timeout=10)
    except Exception:
        _save_profiles_local(data)


def get_profile(user_id: str) -> UserProfile | None:
    raw = _load_profiles().get(user_id)
    if not isinstance(raw, dict):
        return None
    try:
        fields = {k: v for k, v in raw.items() if k in UserProfile.__dataclass_fields__}
        return UserProfile(**fields)
    except Exception:
        return None


def save_profile(user_id: str, birth_date: str, birth_time: str = "", gender: str = "", calendar: str = "solar") -> UserProfile:
    data = _load_profiles()
    old = data.get(user_id, {}) if isinstance(data.get(user_id), dict) else {}
    now = _now()
    profile = UserProfile(
        user_id=user_id,
        birth_date=birth_date,
        birth_time=birth_time,
        gender=gender,
        calendar=calendar,
        created_at=str(old.get("created_at") or now),
        updated_at=now,
    )
    data[user_id] = asdict(profile)
    _save_profiles(data)
    return profile


def parse_birth_command(text: str) -> tuple[str, str, str, str] | None:
    q = text.strip()
    if not any(key in q for key in ["생년월일", "생일", "출생", "사주등록", "프로필"]):
        return None
    date_match = re.search(r"(\d{4})[-./년\s]*(\d{1,2})[-./월\s]*(\d{1,2})", q)
    if not date_match:
        return None
    y, m, d = date_match.groups()
    birth_date = f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    time_match = re.search(r"(\d{1,2})[:시]\s*(\d{1,2})?", q)
    birth_time = ""
    if time_match:
        hh = int(time_match.group(1))
        mm = int(time_match.group(2) or 0)
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            birth_time = f"{hh:02d}:{mm:02d}"
    gender = "여" if re.search(r"\b여\b|여자|여성", q) else "남" if re.search(r"\b남\b|남자|남성", q) else ""
    calendar = "lunar" if "음력" in q else "solar"
    return birth_date, birth_time, gender, calendar


def _stem_index(profile: UserProfile) -> int:
    key = f"{profile.birth_date} {profile.birth_time} {profile.gender}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % 10


def saju_reading(profile: UserProfile, question: str = "") -> str:
    try:
        from .saju_engine import reading as engine_reading
        return engine_reading(name=profile.user_id, profile=profile, question=question)
    except Exception:
        pass
    stems = ["목", "화", "토", "금", "수", "목화", "화토", "토금", "금수", "수목"]
    tone = stems[_stem_index(profile)]
    if any(w in question for w in ["연애", "결혼", "관계"]):
        focus = "관계운: 상대의 말보다 반복 행동을 기준으로 봐야 한다. 감정 확인보다 생활 구조 합의가 먼저다."
    elif any(w in question for w in ["돈", "재물", "투자", "주식"]):
        focus = "재물운: 한 번에 크게 먹는 흐름보다 손실 제한, 현금 비중, 반복 가능한 규칙이 중요하다."
    elif any(w in question for w in ["직장", "일", "시험", "공부"]):
        focus = "일/학업운: 체면보다 루틴과 산출물이 중요하다. 작은 마감 단위로 쪼개야 성과가 난다."
    else:
        focus = "종합운: 지금 질문은 선택 기준을 명확히 잡는 것이 먼저다. 감정적 확신보다 검증 가능한 조건을 봐야 한다."
    return (
        f"사주 분석\n"
        f"생년월일: {profile.birth_date} {profile.birth_time or '시간미상'} {profile.gender or ''} "
        f"({'음력' if profile.calendar == 'lunar' else '양력'})\n"
        f"핵심 기운: {tone} 기운 중심\n"
        f"{focus}\n"
        f"주의: 자동 간이 해석입니다. 정밀 명식은 출생지·절기 기준 확인 필요."
    )


def tarot_reading(user_id: str, question: str = "") -> str:
    seed = f"{user_id}:{datetime.now().date()}:{question.strip()}"
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    cards = rng.sample(TAROT_MAJOR, 3)
    labels = ["현재", "장애", "조언"]
    lines = ["타로 3카드 리딩"]
    for label, (name, meaning) in zip(labels, cards):
        lines.append(f"{label}: {name} - {meaning}")
    if question:
        lines.append(f"질문: {question.strip()[:80]}")
    lines.append("판단: 타로는 의사결정 보조용이다. 돈·법률·건강 문제는 사실 확인을 우선한다.")
    return "\n".join(lines)


def _yahoo_chart(symbol: str) -> dict[str, Any] | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    try:
        r = requests.get(url, params={"range": "1d", "interval": "1m"}, timeout=QUOTE_TIMEOUT)
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        meta = result[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        prev = meta.get("chartPreviousClose") or meta.get("previousClose")
        currency = meta.get("currency") or ""
        exchange = meta.get("exchangeName") or meta.get("exchangeTimezoneName") or ""
        if price is None:
            return None
        change_pct = None
        if prev:
            change_pct = (float(price) - float(prev)) / float(prev) * 100
        return {"symbol": symbol, "price": float(price), "prev": prev, "change_pct": change_pct, "currency": currency, "exchange": exchange}
    except Exception:
        return None


def _quote_candidates(query: str) -> list[str]:
    q = query.strip()
    lower = q.lower()
    if lower in KR_NAME_TO_CODE:
        code = KR_NAME_TO_CODE[lower]
        return [f"{code}.KS", f"{code}.KQ"]
    if q in KR_NAME_TO_CODE:
        code = KR_NAME_TO_CODE[q]
        return [f"{code}.KS", f"{code}.KQ"]
    if re.fullmatch(r"\d{6}", q):
        return [f"{q}.KS", f"{q}.KQ"]
    if re.fullmatch(r"[A-Za-z.\-]{1,10}", q):
        return [q.upper()]
    return []


def quote_text(query: str) -> str:
    candidates = _quote_candidates(query)
    if not candidates:
        return "시세 형식: 시세 삼성전자 / 시세 005930 / 시세 NVDA"
    for symbol in candidates:
        item = _yahoo_chart(symbol)
        if item:
            pct = item["change_pct"]
            pct_text = "등락률 미확인" if pct is None else f"{pct:+.2f}%"
            price = item["price"]
            price_text = f"{price:,.0f}" if price >= 1000 else f"{price:,.2f}"
            return (
                f"시세 {query}\n"
                f"{item['symbol']}: {price_text} {item['currency']} ({pct_text})\n"
                f"거래소: {item['exchange']}\n"
                f"출처: Yahoo Finance (지연 데이터, 주문 전 증권사 재확인)"
            )
    return f"시세를 찾지 못했습니다: {query}"


def help_text() -> str:
    return (
        "📋 명령어 안내\n"
        "봇 뉴스 — 최신 수집 뉴스/시황\n"
        "봇 뉴스갱신 — 새로 수집 후 표시\n"
        "봇 시세 삼성전자 / 봇 시세 005930 / 봇 시세 NVDA\n"
        "봇 생년월일 1987-12-28 08:30 여 — 사주 프로필 저장 (재시작 후에도 유지)\n"
        "봇 사주 [질문] — 저장된 생년월일 기반 분석\n"
        "봇 타로 [질문] — 3카드 리딩\n"
        "봇 도움말 — 이 안내"
    )


def _command_body(message: str) -> str:
    msg = message.strip()
    if msg == "봇":
        return "도움말"
    for prefix in ["봇 ", "봇:", "봇아 "]:
        if msg.startswith(prefix):
            return msg[len(prefix):].strip()
    return msg


def _manual_news_refresh() -> str:
    try:
        from .app import generate_report
        return generate_report(hours=1, limit=999, briefing_kind="manual", collect=True, send=False, source="telegram_manual")
    except Exception as exc:
        return f"뉴스갱신 실패: {type(exc).__name__}: {exc}"


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    msg = _command_body(message)

    birth = parse_birth_command(msg)
    if birth:
        profile = save_profile(user_id, *birth)
        cal = "음력" if profile.calendar == "lunar" else "양력"
        return (
            f"프로필 저장 완료 ✅\n"
            f"{profile.birth_date} {profile.birth_time or '시간미상'} "
            f"{profile.gender or ''} ({cal})\n"
            f"이후 서버 재시작해도 사주 정보가 유지됩니다.\n"
            f"'봇 사주'로 바로 조회하세요."
        )

    q = msg.replace(" ", "").lower()
    if q in {"도움", "도움말", "help", "/help", "?"}:
        return help_text()
    if q in {"뉴스갱신", "/뉴스갱신", "새뉴스", "refresh"}:
        return _manual_news_refresh()
    if q in {"뉴스", "/뉴스", "!뉴스", "news", "/news", "시황", "브리핑"}:
        return latest_report or "뉴스 없음"
    if msg.startswith("시세") or msg.lower().startswith("quote"):
        target = re.sub(r"^(시세|quote)\s*", "", msg, flags=re.IGNORECASE).strip()
        return quote_text(target)
    if msg.startswith("사주") or msg.startswith("운세"):
        profile = get_profile(user_id)
        if not profile:
            return "먼저 생년월일을 등록하세요.\n예: 봇 생년월일 1987-12-28 08:30 여"
        question = re.sub(r"^(사주|운세)\s*", "", msg).strip()
        return saju_reading(profile, question)
    if msg.startswith("타로"):
        question = re.sub(r"^타로\s*", "", msg).strip()
        return tarot_reading(user_id, question)
    return "명령어를 인식하지 못했습니다. '봇 도움말'을 입력하세요."
