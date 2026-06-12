from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re
from datetime import datetime
from typing import Any

import requests

from . import bot_services as base
from .symbol_resolver import resolve_symbols

base.PROFILE_PATH = Path(os.getenv("BOT_PROFILE_PATH", "data/bot_profiles.json"))
QUOTE_TIMEOUT = base.QUOTE_TIMEOUT


def _strip_bot_prefix(message: str) -> tuple[bool, str]:
    msg = str(message or "").strip()
    if msg == "봇":
        return True, "도움말"
    for prefix in ("봇 ", "봇:", "봇아 "):
        if msg.startswith(prefix):
            return True, msg[len(prefix):].strip()
    return False, msg


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", str(value or "").lower())


def _code_from_symbol(query: str) -> str | None:
    q = str(query or "").strip()
    digits = re.sub(r"\D", "", q)
    if len(digits) == 6:
        return digits
    for sym in resolve_symbols(q, categories=["kr_stock"], raw_tickers=[]):
        if sym.asset_type == "stock_kr":
            return sym.ticker.upper().replace(".KS", "").replace(".KQ", "")
    return None


def _yahoo_symbol(query: str) -> str:
    q = str(query or "").strip()
    digits = re.sub(r"\D", "", q)
    if len(digits) == 6:
        return f"{digits}.KS"
    for sym in resolve_symbols(q, categories=["kr_stock", "us_stock"], raw_tickers=[]):
        if sym.asset_type == "stock_kr":
            return sym.ticker
        if sym.asset_type in {"stock_us", "stock_or_us"}:
            return sym.ticker
    return q.upper()


def _simple_ma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(values[-period - 1:-1], values[-period:]):
        diff = cur - prev
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _score_from_history(closes: list[float], volumes: list[float]) -> tuple[int, str, float | None, float | None, float | None, float | None, float | None]:
    if not closes:
        return 0, "가격 데이터 없음", None, None, None, None, None
    price = closes[-1]
    ma20 = _simple_ma(closes, 20)
    ma60 = _simple_ma(closes, 60)
    ma120 = _simple_ma(closes, 120)
    rsi14 = _rsi(closes, 14)
    score = 50
    reasons: list[str] = []
    if ma20 and price > ma20:
        score += 10
        reasons.append("20일선 상회")
    if ma60 and price > ma60:
        score += 10
        reasons.append("60일선 상회")
    if ma20 and ma60 and ma20 > ma60:
        score += 10
        reasons.append("단기 추세 우위")
    if rsi14 is not None:
        if 45 <= rsi14 <= 70:
            score += 10
            reasons.append("RSI 정상 모멘텀")
        elif rsi14 > 78:
            score -= 15
            reasons.append("RSI 과열")
        elif rsi14 < 35:
            score -= 10
            reasons.append("RSI 약세")
    vol_ratio = None
    if len(volumes) >= 21:
        avg_vol = sum(volumes[-21:-1]) / 20
        if avg_vol > 0:
            vol_ratio = volumes[-1] / avg_vol
            if vol_ratio >= 1.5:
                score += 10
                reasons.append("거래량 증가")
    return max(0, min(100, score)), ", ".join(reasons) or "중립", ma20, ma60, ma120, rsi14, vol_ratio


def _pykrx_history(code: str) -> dict[str, Any] | None:
    try:
        from pykrx import stock
        end = datetime.now().strftime("%Y%m%d")
        start = f"{datetime.now().year - 1}{datetime.now().month:02d}{datetime.now().day:02d}"
        df = stock.get_market_ohlcv_by_date(start, end, code)
        if df is None or df.empty:
            return None
        closes = [float(x) for x in df["종가"].tail(130).tolist()]
        volumes = [float(x) for x in df["거래량"].tail(130).tolist()]
        name = stock.get_market_ticker_name(code) or code
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) >= 2 else last
        price = float(last["종가"])
        prev_close = float(prev["종가"])
        pct = ((price / prev_close) - 1) * 100 if prev_close else 0.0
        return {
            "symbol": f"{name}({code})",
            "price": price,
            "pct": pct,
            "currency": "KRW",
            "exchange": "KRX(pykrx)",
            "closes": closes,
            "volumes": volumes,
        }
    except Exception:
        return None


def _yahoo_history(symbol: str) -> dict[str, Any] | None:
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"range": "6mo", "interval": "1d"}
        res = requests.get(url, params=params, timeout=min(3.0, float(QUOTE_TIMEOUT)))
        if res.status_code != 200:
            return None
        data = res.json()["chart"]["result"][0]
        meta = data.get("meta") or {}
        quote = data.get("indicators", {}).get("quote", [{}])[0]
        closes = [float(x) for x in quote.get("close", []) if x is not None]
        volumes = [float(x) for x in quote.get("volume", []) if x is not None]
        if not closes:
            return None
        price = float(meta.get("regularMarketPrice") or closes[-1])
        prev = float(meta.get("previousClose") or (closes[-2] if len(closes) >= 2 else price))
        pct = ((price / prev) - 1) * 100 if prev else 0.0
        return {
            "symbol": meta.get("shortName") or symbol,
            "price": price,
            "pct": pct,
            "currency": meta.get("currency") or "",
            "exchange": meta.get("exchangeName") or "Yahoo",
            "closes": closes,
            "volumes": volumes,
        }
    except Exception:
        return None


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "미확인"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def quote_text(query: str) -> str:
    q = str(query or "").strip()
    if not q:
        return "시세 대상을 입력하세요. 예: 봇 시세 삼성전자"
    code = _code_from_symbol(q)
    item = _pykrx_history(code) if code else None
    symbol = _yahoo_symbol(q)
    if not item:
        item = _yahoo_history(symbol)
    if not item and code:
        alt_suffix = ".KQ" if symbol.endswith(".KS") else ".KS"
        item = _yahoo_history(f"{code}{alt_suffix}")
    if not item:
        return f"시세를 찾지 못했습니다: {q}\n종목명/코드 판별 실패 또는 외부 시세 응답 실패입니다. 예: 봇 시세 삼성전자 / 봇 시세 005930 / 봇 시세 NVDA"

    closes = item["closes"]
    volumes = item["volumes"]
    score, reason, ma20, ma60, ma120, rsi14, vol_ratio = _score_from_history(closes, volumes)
    price = item["price"]
    pct = item["pct"]
    support = min(closes[-20:]) if len(closes) >= 20 else min(closes)
    resistance = max(closes[-20:]) if len(closes) >= 20 else max(closes)
    stop = support * 0.98
    target = resistance * 1.03
    if score >= 75:
        call = "관심/분할 접근 가능"
    elif score >= 55:
        call = "관망 우위"
    else:
        call = "비추천/리스크 우위"
    pct_text = f"{pct:+.2f}%"
    rsi_text = "미확인" if rsi14 is None else f"{rsi14:.1f}"
    vol_text = "미확인" if vol_ratio is None else f"{vol_ratio:.1f}배"
    return (
        f"금융퀀트 시세/판단: {q}\n"
        f"{item['symbol']}: {_fmt_price(price)} {item['currency']} ({pct_text})\n"
        f"거래소: {item['exchange']}\n"
        f"기술점수: {score}/100 | RSI14 {rsi_text} | 거래량 {vol_text}\n"
        f"MA20/60/120: {_fmt_price(ma20)} / {_fmt_price(ma60)} / {_fmt_price(ma120)}\n"
        f"20일 지지/저항: {_fmt_price(support)} / {_fmt_price(resistance)}\n"
        f"추천: {call}\n"
        f"전략: 진입은 현재가 기준 분할, 손절 {_fmt_price(stop)}, 1차목표 {_fmt_price(target)}\n"
        f"근거: {reason}\n"
        f"주의: 뉴스/시세 봇의 간이 판단입니다. 주문 전 거래소 현재가와 시장 전체 방향을 재확인하세요."
    )


def _profile_seed(profile: base.UserProfile, question: str) -> int:
    key = f"{profile.birth_date}|{profile.birth_time}|{profile.gender}|{profile.calendar}|{question}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)


def _profile_summary(user_id: str) -> str:
    profile = base.get_profile(user_id)
    if not profile:
        return "저장된 생년월일이 없습니다. 최초 1회만 입력하세요. 예: 봇 생년월일 1987-12-28 08:30 여"
    return (
        "프로필 저장됨\n"
        f"생년월일: {profile.birth_date} {profile.birth_time or '시간미상'} {profile.gender or ''} ({'음력' if profile.calendar == 'lunar' else '양력'})\n"
        "앞으로 같은 사용자 ID로 들어오면 사주 명령에서 자동 사용합니다."
    )


def _private_saju(user_id: str, msg: str) -> str:
    profile = base.get_profile(user_id)
    if not profile:
        return "먼저 생년월일을 등록하세요. 예: 봇 생년월일 YYYY-MM-DD HH:MM 여"
    question = re.sub(r"^(사주|운세)\s*", "", msg).strip()
    seed = _profile_seed(profile, question)
    patterns = ["비겁", "식상", "재성", "관성", "인성"]
    useful = ["목", "화", "토", "금", "수"][seed % 5]
    pressure = patterns[(seed // 7) % 5]
    flow = ["초반 정리-중반 실행-후반 회수", "관계 조율 후 실행", "현금/체력 보존 후 재진입", "공식 절차와 문서화 우선", "작게 반복해 신뢰 회복"][(seed // 13) % 5]
    if any(w in question for w in ["돈", "재물", "투자", "주식", "매매"]):
        focus = "재물: 큰 베팅보다 손실 한도와 현금비중이 핵심이다. 추세가 확인된 자산만 분할 접근하고, 감정적 물타기는 금지."
    elif any(w in question for w in ["연애", "결혼", "관계", "상대"]):
        focus = "관계: 말의 확신보다 반복 행동, 책임 분담, 생활 리듬 일치가 핵심 판단 기준이다."
    elif any(w in question for w in ["직장", "일", "시험", "공부", "이직"]):
        focus = "일/학업: 체면보다 산출물, 루틴, 마감 단위가 중요하다. 평가받는 구조에 몸을 넣어야 성과가 난다."
    else:
        focus = "종합: 지금은 방향보다 구조를 먼저 잡아야 한다. 감정 판단을 줄이고, 검증 가능한 기준으로 선택해야 한다."
    try:
        from .advice_variants import pick_variant
        category = "money" if any(w in question for w in ["돈", "재물", "투자", "주식", "매매"]) else "relationship" if any(w in question for w in ["연애", "결혼", "관계", "상대"]) else "work" if any(w in question for w in ["직장", "일", "시험", "공부", "이직"]) else "general"
        action, caution = pick_variant(f"{profile.birth_date}|{profile.birth_time}|{profile.gender}|{profile.calendar}|{question}", category)
    except Exception:
        action = "오늘 할 일 1개를 정하고 완료 기준을 숫자로 적어라."
        caution = "확신이 강할수록 반대 근거 1개를 먼저 확인해야 한다."
    return (
        "전문가식 사주 리딩\n"
        "저장된 비공개 프로필 기준으로 해석함\n"
        f"핵심 축: {pressure} 이슈가 강하게 작동하는 흐름\n"
        f"보완 기운: {useful} 성향을 생활·일·관계에서 의식적으로 보강\n"
        f"운의 흐름: {flow}\n"
        f"{focus}\n"
        f"실행 조언: {action}\n"
        f"이번 주 점검: {caution}\n"
        "주의: 채팅에는 생년월일을 재표시하지 않는다. 정확한 명식은 절기·출생지·음양력 검증 후 별도 계산 필요."
    )


def _private_tarot(user_id: str, question: str = "") -> str:
    seed = f"{user_id}:{datetime.now().date()}:{question.strip()}"
    import random
    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).hexdigest())
    cards = rng.sample(base.TAROT_MAJOR, 3)
    labels = ["현재 에너지", "막힌 지점", "실행 조언"]
    lines = ["전문가식 타로 3카드 리딩"]
    for label, (name, meaning) in zip(labels, cards):
        lines.append(f"{label}: {name}")
        lines.append(f"- 해석: {meaning}")
    if question:
        lines.append(f"질문 초점: {question.strip()[:80]}")
    lines.append("종합판단: 지금 필요한 것은 예언이 아니라 선택 기준이다. 카드가 가리키는 리스크를 현실 행동으로 줄여야 한다.")
    lines.append("주의: 투자·법률·건강은 타로보다 사실 확인과 손실 제한을 우선한다.")
    return "\n".join(lines)


def help_text() -> str:
    return (
        "명령어는 반드시 '봇'으로 시작\n"
        "봇 뉴스 - 최신 중요 뉴스/시황\n"
        "봇 시세 삼성전자 / 봇 시세 한미반도체 / 봇 시세 005930 / 봇 시세 NVDA\n"
        "봇 생년월일 YYYY-MM-DD HH:MM 성별 - 최초 1회 프로필 저장\n"
        "봇 프로필확인 - 저장된 생년월일 확인\n"
        "봇 사주 [질문] - 저장 프로필 기반 전문가식 리딩\n"
        "봇 타로 [질문] - 전문가식 3카드 리딩\n"
        "봇 도움말 - 명령어 안내"
    )


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    has_prefix, msg = _strip_bot_prefix(message)
    if not has_prefix:
        return "명령어는 '봇'으로 시작해야 합니다. 예: 봇 뉴스"
    birth = base.parse_birth_command(msg)
    if birth:
        profile = base.save_profile(user_id, *birth)
        return (
            "프로필 저장 완료\n"
            f"{profile.birth_date} {profile.birth_time or '시간미상'} {profile.gender or ''} ({'음력' if profile.calendar == 'lunar' else '양력'})\n"
            "앞으로 같은 사용자 ID에서는 생년월일을 다시 입력하지 않아도 됩니다.\n"
            "서버가 임시 파일시스템이면 BOT_PROFILE_PATH를 영구 저장 경로로 지정해야 재시작 후에도 유지됩니다."
        )

    q = msg.lower().strip()
    compact = q.replace(" ", "")
    if q in {"도움", "도움말", "help", "/help", "?"}:
        return help_text()
    if compact in {"프로필", "프로필확인", "내프로필", "생년월일확인", "사주프로필"}:
        return _profile_summary(user_id)
    if q in {"뉴스", "/뉴스", "!뉴스", "news", "/news", "시황", "브리핑"}:
        return latest_report or "뉴스 없음"
    if msg.startswith("시세") or msg.lower().startswith("quote"):
        target = re.sub(r"^(시세|quote)\s*", "", msg, flags=re.IGNORECASE).strip()
        return quote_text(target)
    if msg.startswith("사주") or msg.startswith("운세"):
        return _private_saju(user_id, msg)
    if msg.startswith("타로"):
        question = re.sub(r"^타로\s*", "", msg).strip()
        return _private_tarot(user_id, question)
    return "명령어를 인식하지 못했습니다. '봇 도움말'을 입력하세요."
