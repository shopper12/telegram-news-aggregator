from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import os
import re
from typing import Any

import requests

from . import bot_services as base

base.PROFILE_PATH = Path(os.getenv("BOT_PROFILE_PATH", "/var/data/bot_profiles.json"))

QUOTE_TIMEOUT = base.QUOTE_TIMEOUT
_KR_NAME_CACHE: dict[str, str] | None = None

from pathlib import Path
import os

base.PROFILE_PATH = Path(os.getenv("BOT_PROFILE_PATH", "/var/data/bot_profiles.json"))

from __future__ import annotations

from datetime import datetime
import hashlib
import re
from typing import Any

import requests

from . import bot_services as base

QUOTE_TIMEOUT = base.QUOTE_TIMEOUT
_KR_NAME_CACHE: dict[str, str] | None = None


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", value.strip().lower())


def _strip_bot_prefix(message: str) -> tuple[bool, str]:
    msg = message.strip()
    if msg == "봇":
        return True, "도움말"
    if msg.startswith("봇 "):
        return True, msg[2:].strip()
    if msg.startswith("봇:"):
        return True, msg[2:].strip()
    if msg.startswith("봇아 "):
        return True, msg[3:].strip()
    return False, msg


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


def _simple_ma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def _rsi(values: list[float], window: int = 14) -> float | None:
    if len(values) <= window:
        return None
    gains = []
    losses = []
    for i in range(-window, 0):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    avg_gain = sum(gains) / window
    avg_loss = sum(losses) / window
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _atr(highs: list[float], lows: list[float], closes: list[float], window: int = 14) -> float | None:
    if len(closes) <= window or len(highs) != len(lows) or len(highs) != len(closes):
        return None
    trs = []
    start = len(closes) - window
    for i in range(start, len(closes)):
        prev_close = closes[i - 1]
        tr = max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def _yahoo_history(symbol: str) -> dict[str, Any] | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "6mo", "interval": "1d"},
            timeout=QUOTE_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        result = data.get("chart", {}).get("result", [])
        if not result:
            return None
        obj = result[0]
        meta = obj.get("meta", {})
        quote = (obj.get("indicators", {}).get("quote") or [{}])[0]
        closes = [float(x) for x in quote.get("close", []) if x is not None]
        highs = [float(x) for x in quote.get("high", []) if x is not None]
        lows = [float(x) for x in quote.get("low", []) if x is not None]
        volumes = [float(x) for x in quote.get("volume", []) if x is not None]
        if not closes:
            return None
        price = float(meta.get("regularMarketPrice") or closes[-1])
        prev = float(meta.get("chartPreviousClose") or closes[-2] if len(closes) > 1 else closes[-1])
        return {
            "symbol": symbol,
            "price": price,
            "prev": prev,
            "pct": (price - prev) / prev * 100 if prev else None,
            "currency": meta.get("currency") or "",
            "exchange": meta.get("exchangeName") or meta.get("exchangeTimezoneName") or "",
            "closes": closes,
            "highs": highs[-len(closes):],
            "lows": lows[-len(closes):],
            "volumes": volumes[-len(closes):],
        }
    except Exception:
        return None


def _code_from_symbol(symbol: str) -> str | None:
    m = re.match(r"^(\d{6})(?:\.(?:KS|KQ))?$", symbol, re.IGNORECASE)
    return m.group(1) if m else None


def _pykrx_history(code: str) -> dict[str, Any] | None:
    try:
        from pykrx import stock
        from datetime import datetime, timedelta

        end = datetime.now()
        start = end - timedelta(days=260)
        df = stock.get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"),
            end.strftime("%Y%m%d"),
            code,
        )
        if df is None or df.empty:
            return None

        closes = [float(x) for x in df["종가"].dropna().tolist()]
        highs = [float(x) for x in df["고가"].dropna().tolist()]
        lows = [float(x) for x in df["저가"].dropna().tolist()]
        volumes = [float(x) for x in df["거래량"].dropna().tolist()]

        if len(closes) < 2:
            return None

        price = closes[-1]
        prev = closes[-2]
        name = stock.get_market_ticker_name(code) or code

        return {
            "symbol": f"{name}({code})",
            "price": price,
            "prev": prev,
            "pct": (price - prev) / prev * 100 if prev else None,
            "currency": "KRW",
            "exchange": "KRX",
            "source": "pykrx KRX daily OHLCV",
            "closes": closes,
            "highs": highs[-len(closes):],
            "lows": lows[-len(closes):],
            "volumes": volumes[-len(closes):],
        }
    except Exception:
        return None

def _quote_candidates(query: str) -> list[str]:
    q = query.strip()
    lower = q.lower()
    if lower in base.KR_NAME_TO_CODE:
        code = base.KR_NAME_TO_CODE[lower]
        return [f"{code}.KS", f"{code}.KQ", code]
    if q in base.KR_NAME_TO_CODE:
        code = base.KR_NAME_TO_CODE[q]
        return [f"{code}.KS", f"{code}.KQ", code]
    if re.fullmatch(r"\d{6}", q):
        return [f"{q}.KS", f"{q}.KQ"]
    if re.fullmatch(r"[A-Za-z.\-]{1,10}", q):
        return [q.upper()]
    out: list[str] = []
    for code in _kr_code_matches(q):
        out.extend([f"{code}.KS", f"{code}.KQ"])
    return out


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "미확인"
    return f"{value:,.0f}" if value >= 1000 else f"{value:,.2f}"


def quote_text(query: str) -> str:
    q = query.strip()
    candidates = _quote_candidates(q)
    if not candidates:
        return "시세 형식: 봇 시세 삼성전자 / 봇 시세 한미반도체 / 봇 시세 005930 / 봇 시세 NVDA"
    seen = set()
    for symbol in candidates:
        if symbol in seen:
            continue
        seen.add(symbol)
        item = _yahoo_history(symbol)
        if not item:
            code = _code_from_symbol(symbol)
            if code:
                item = _pykrx_history(code)
        if not item:
            continue
        closes = item["closes"]
        highs = item["highs"]
        lows = item["lows"]
        volumes = item["volumes"]
        price = item["price"]
        ma20 = _simple_ma(closes, 20)
        ma60 = _simple_ma(closes, 60)
        ma120 = _simple_ma(closes, 120)
        rsi14 = _rsi(closes, 14)
        atr14 = _atr(highs, lows, closes, 14)
        support = min(lows[-20:]) if len(lows) >= 20 else None
        resistance = max(highs[-20:]) if len(highs) >= 20 else None
        vol20 = _simple_ma(volumes, 20) if volumes else None
        vol_ratio = volumes[-1] / vol20 if vol20 and volumes else None

        score = 50
        if ma20 and price > ma20:
            score += 10
        if ma60 and price > ma60:
            score += 10
        if ma20 and ma60 and ma20 > ma60:
            score += 10
        if ma120 and price > ma120:
            score += 5
        if rsi14 is not None:
            if 45 <= rsi14 <= 65:
                score += 10
            elif 65 < rsi14 <= 75:
                score += 3
            elif rsi14 > 75:
                score -= 15
            elif rsi14 < 35:
                score -= 8
        if resistance and price >= resistance * 0.995:
            score += 7
        if support and price <= support * 1.03:
            score += 5
        if vol_ratio and vol_ratio >= 1.5:
            score += 5
        score = max(0, min(100, score))

        if score >= 75:
            call = "분할매수 후보"
            reason = "추세와 수급 점수가 우세하다. 단, 한 번에 진입하지 말고 분할 접근."
        elif score >= 60:
            call = "눌림대기/소액관찰"
            reason = "상승 조건은 일부 충족하지만 추격 매수는 제한한다."
        elif score >= 45:
            call = "관망"
            reason = "방향성 우위가 약하다. 지지/저항 확인 전 신규 진입 보류."
        else:
            call = "매수 보류"
            reason = "추세 또는 모멘텀 점수가 낮다. 손실 회피가 우선."

        stop = price - atr14 * 1.5 if atr14 else None
        target = price + atr14 * 2.0 if atr14 else resistance
        pct_text = "등락률 미확인" if item["pct"] is None else f"{item['pct']:+.2f}%"
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
            f"주의: Yahoo 지연/장외 데이터일 수 있어 실제 주문 전 거래소 현재가 재확인."
        )
    return f"시세를 찾지 못했습니다: {q}"


def _profile_seed(profile: base.UserProfile, question: str) -> int:
    key = f"{profile.birth_date}|{profile.birth_time}|{profile.gender}|{profile.calendar}|{question}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)


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

    return (
        "전문가식 사주 리딩\n"
        "비공개 프로필 기준으로 해석함\n"
        f"핵심 축: {pressure} 이슈가 강하게 작동하는 흐름\n"
        f"보완 기운: {useful} 성향을 생활·일·관계에서 의식적으로 보강\n"
        f"운의 흐름: {flow}\n"
        f"{focus}\n"
        "실행 조언: 오늘 할 일 1개, 이번 주 버릴 일 1개, 손실 제한선 1개를 숫자로 정하라.\n"
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
        "봇 생년월일 YYYY-MM-DD HH:MM 성별 - 사주 프로필 비공개 저장\n"
        "봇 사주 [질문] - 비공개 프로필 기반 전문가식 리딩\n"
        "봇 타로 [질문] - 전문가식 3카드 리딩\n"
        "봇 도움말 - 명령어 안내"
    )


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    has_prefix, msg = _strip_bot_prefix(message)
    if not has_prefix:
        return "명령어는 '봇'으로 시작해야 합니다. 예: 봇 뉴스"
    birth = base.parse_birth_command(msg)
    if birth:
        base.save_profile(user_id, *birth)
        return "프로필 저장 완료\n생년월일은 채팅 답변에 다시 표시하지 않습니다. 이후 '봇 사주 질문'으로 조회하세요."

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
        return _private_tarot(user_id, question)
    return "명령어를 인식하지 못했습니다. '봇 도움말'을 입력하세요."
