from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import json
import os
import random
import re
from typing import Any

import requests

from . import bot_services as base

PROFILE_PATH = Path(os.getenv("BOT_PROFILE_PATH", "data/bot_profiles.json"))
QUOTE_TIMEOUT = float(os.getenv("QUOTE_TIMEOUT_SECONDS", "4"))
_KR_NAME_CACHE: dict[str, str] | None = None

EXTRA_KR_NAME_TO_CODE = {
    "삼성전자": "005930",
    "sk하이닉스": "000660",
    "하이닉스": "000660",
    "한미반도체": "042700",
    "두산에너빌리티": "034020",
    "현대차": "005380",
    "기아": "000270",
    "네이버": "035420",
    "naver": "035420",
    "카카오": "035720",
    "lg전자": "066570",
    "lg에너지솔루션": "373220",
    "셀트리온": "068270",
    "알테오젠": "196170",
    "파마리서치": "214450",
    "리가켐바이오": "141080",
    "에코프로": "086520",
    "에코프로비엠": "247540",
}


def _normalize_name(value: str) -> str:
    return re.sub(r"[\s·().,㈜주식회사_-]+", "", str(value or "").strip().lower())


def _strip_bot_prefix(message: str) -> tuple[bool, str]:
    msg = str(message or "").strip()
    if msg == "봇":
        return True, "도움말"
    for prefix in ["봇 ", "봇:", "봇아 "]:
        if msg.startswith(prefix):
            return True, msg[len(prefix):].strip()
    return False, msg


def _load_profiles() -> dict[str, dict[str, Any]]:
    if not PROFILE_PATH.exists():
        return {}
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_profiles(data: dict[str, dict[str, Any]]) -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _get_profile(user_id: str) -> base.UserProfile | None:
    raw = _load_profiles().get(user_id)
    if not isinstance(raw, dict):
        return None
    try:
        return base.UserProfile(**raw)
    except Exception:
        return None


def _save_profile(user_id: str, birth_date: str, birth_time: str = "", gender: str = "", calendar: str = "solar") -> None:
    data = _load_profiles()
    old = data.get(user_id, {}) if isinstance(data.get(user_id), dict) else {}
    now = datetime.now().isoformat(timespec="seconds")
    profile = base.UserProfile(
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


def _load_kr_names() -> dict[str, str]:
    global _KR_NAME_CACHE
    if _KR_NAME_CACHE is not None:
        return _KR_NAME_CACHE
    names: dict[str, str] = {}
    for name, code in {**base.KR_NAME_TO_CODE, **EXTRA_KR_NAME_TO_CODE}.items():
        names[_normalize_name(name)] = code
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
        pass
    _KR_NAME_CACHE = names
    return names


def _kr_code_matches(query: str, limit: int = 10) -> list[str]:
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
    return [code for name, code in names.items() if key in name][:limit]


def _simple_ma(values: list[float], window: int) -> float | None:
    return sum(values[-window:]) / window if len(values) >= window else None


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
    for i in range(len(closes) - window, len(closes)):
        prev_close = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - prev_close), abs(lows[i] - prev_close)))
    return sum(trs) / len(trs) if trs else None


def _krx_history(code: str) -> dict[str, Any] | None:
    try:
        from pykrx import stock
        end = datetime.now()
        start = end - timedelta(days=260)
        df = stock.get_market_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), code)
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


def _yahoo_history(symbol: str) -> dict[str, Any] | None:
    try:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": "6mo", "interval": "1d"},
            timeout=QUOTE_TIMEOUT,
        )
        if response.status_code != 200:
            return None
        result = response.json().get("chart", {}).get("result", [])
        if not result:
            return None
        obj = result[0]
        meta = obj.get("meta", {})
        quote = (obj.get("indicators", {}).get("quote") or [{}])[0]
        closes = [float(x) for x in quote.get("close", []) if x is not None]
        highs = [float(x) for x in quote.get("high", []) if x is not None]
        lows = [float(x) for x in quote.get("low", []) if x is not None]
        volumes = [float(x) for x in quote.get("volume", []) if x is not None]
        if len(closes) < 2:
            return None
        price = float(meta.get("regularMarketPrice") or closes[-1])
        prev = float(meta.get("chartPreviousClose") or closes[-2])
        return {
            "symbol": symbol,
            "price": price,
            "pct": (price - prev) / prev * 100 if prev else None,
            "currency": meta.get("currency") or "",
            "exchange": meta.get("exchangeName") or meta.get("exchangeTimezoneName") or "",
            "source": "Yahoo Finance chart API",
            "closes": closes,
            "highs": highs[-len(closes):],
            "lows": lows[-len(closes):],
            "volumes": volumes[-len(closes):],
        }
    except Exception:
        return None


def _quote_candidates(query: str) -> tuple[list[str], bool]:
    q = query.strip()
    lower = q.lower()
    if lower in { _normalize_name(k): v for k, v in {**base.KR_NAME_TO_CODE, **EXTRA_KR_NAME_TO_CODE}.items() }:
        code = { _normalize_name(k): v for k, v in {**base.KR_NAME_TO_CODE, **EXTRA_KR_NAME_TO_CODE}.items() }[lower]
        return [code], True
    if re.fullmatch(r"\d{6}", q):
        return [q], True
    if re.fullmatch(r"[A-Za-z.\-]{1,10}", q):
        return [q.upper()], False
    codes = _kr_code_matches(q)
    return codes, True


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "미확인"
    return f"{value:,.0f}" if value >= 1000 else f"{value:,.2f}"


def _score_item(item: dict[str, Any]) -> tuple[int, str, str, str]:
    closes = item["closes"]
    highs = item["highs"]
    lows = item["lows"]
    volumes = item["volumes"]
    price = item["price"]
    ma20 = _simple_ma(closes, 20)
    ma60 = _simple_ma(closes, 60)
    ma120 = _simple_ma(closes, 120)
    rsi14 = _rsi(closes, 14)
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
    rsi_text = "미확인" if rsi14 is None else f"{rsi14:.1f}"
    vol_text = "미확인" if vol_ratio is None else f"{vol_ratio:.1f}배"
    level_text = f"MA20/60/120: {_fmt_price(ma20)} / {_fmt_price(ma60)} / {_fmt_price(ma120)}\n20일 지지/저항: {_fmt_price(support)} / {_fmt_price(resistance)}"
    factor_text = f"기술점수: {score}/100 | RSI14 {rsi_text} | 거래량 {vol_text}"
    return score, call, reason, factor_text + "\n" + level_text


def quote_text(query: str) -> str:
    try:
        q = query.strip()
        candidates, is_korean = _quote_candidates(q)
        if not candidates:
            return "시세 형식: 봇 시세 삼성전자 / 봇 시세 한미반도체 / 봇 시세 005930 / 봇 시세 NVDA"
        seen = set()
        for symbol in candidates:
            if symbol in seen:
                continue
            seen.add(symbol)
            item = _krx_history(symbol) if is_korean else _yahoo_history(symbol)
            if not item:
                continue
            score, call, reason, detail = _score_item(item)
            pct_text = "등락률 미확인" if item["pct"] is None else f"{item['pct']:+.2f}%"
            atr14 = _atr(item["highs"], item["lows"], item["closes"], 14)
            stop = item["price"] - atr14 * 1.5 if atr14 else None
            target = item["price"] + atr14 * 2.0 if atr14 else None
            return (
                f"금융퀀트 시세/판단: {q}\n"
                f"{item['symbol']}: {_fmt_price(item['price'])} {item['currency']} ({pct_text})\n"
                f"거래소: {item['exchange']}\n"
                f"{detail}\n"
                f"추천: {call}\n"
                f"전략: 진입은 현재가 기준 분할, 손절 {_fmt_price(stop)}, 1차목표 {_fmt_price(target)}\n"
                f"근거: {reason}\n"
                f"출처: {item['source']}\n"
                f"주의: 실제 주문 전 증권사 현재가 재확인."
            )
        return f"시세를 찾지 못했습니다: {q}"
    except Exception as exc:
        return f"시세 처리 중 오류: {type(exc).__name__}. 종목명 또는 6자리 코드를 다시 입력하세요."


def _profile_seed(profile: base.UserProfile, question: str) -> int:
    key = f"{profile.birth_date}|{profile.birth_time}|{profile.gender}|{profile.calendar}|{question}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16)


def _private_saju(user_id: str, msg: str) -> str:
    profile = _get_profile(user_id)
    if not profile:
        return "먼저 생년월일을 등록하세요. 예: 봇 생년월일 YYYY-MM-DD HH:MM 여"
    question = re.sub(r"^(사주|운세)\s*", "", msg).strip()
    seed = _profile_seed(profile, question)
    patterns = ["비겁", "식상", "재성", "관성", "인성"]
    useful = ["목", "화", "토", "금", "수"][seed % 5]
    pressure = patterns[(seed // 7) % 5]
    flow = ["초반 정리-중반 실행-후반 회수", "관계 조율 후 실행", "현금/체력 보존 후 재진입", "공식 절차와 문서화 우선", "작게 반복해 신뢰 회복"][(seed // 13) % 5]
    if any(word in question for word in ["돈", "재물", "투자", "주식", "매매"]):
        focus = "재물: 큰 베팅보다 손실 한도와 현금비중이 핵심이다. 추세가 확인된 자산만 분할 접근하고, 감정적 물타기는 금지."
    elif any(word in question for word in ["연애", "결혼", "관계", "상대"]):
        focus = "관계: 말의 확신보다 반복 행동, 책임 분담, 생활 리듬 일치가 핵심 판단 기준이다."
    elif any(word in question for word in ["직장", "일", "시험", "공부", "이직"]):
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
        "주의: 채팅에는 생년월일을 재표시하지 않는다. 무료 저장은 Render 재배포 시 유실될 수 있다."
    )


def _private_tarot(user_id: str, question: str = "") -> str:
    seed = f"{user_id}:{datetime.now().date()}:{question.strip()}"
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
    try:
        has_prefix, msg = _strip_bot_prefix(message)
        if not has_prefix:
            return "명령어는 '봇'으로 시작해야 합니다. 예: 봇 뉴스"
        birth = base.parse_birth_command(msg)
        if birth:
            _save_profile(user_id or "default", *birth)
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
            return _private_saju(user_id or "default", msg)
        if msg.startswith("타로"):
            question = re.sub(r"^타로\s*", "", msg).strip()
            return _private_tarot(user_id or "default", question)
        return "명령어를 인식하지 못했습니다. '봇 도움말'을 입력하세요."
    except Exception as exc:
        return f"명령 처리 중 오류: {type(exc).__name__}"
