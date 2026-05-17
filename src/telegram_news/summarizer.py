from __future__ import annotations

from dataclasses import dataclass
from .normalizer import DedupedItem
from .extractor import extract_signals


@dataclass(frozen=True)
class SummaryItem:
    title: str
    body: str
    channels: list[str]
    categories: list[str]
    repeat_count: int
    sectors: list[str]
    keywords: list[str]
    tickers: list[str]
    importance_score: int
    judgment: str
    trade_view: str
    risk: str


def _make_title(text: str, max_len: int = 72) -> str:
    cleaned = " ".join(text.replace("\n", " ").split())
    for splitter in [" - ", " | ", " / ", "[", "("]:
        if splitter in cleaned and len(cleaned) > max_len:
            cleaned = cleaned.split(splitter)[0].strip()
            break
    return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


def _contains_any(text: str, words: list[str]) -> bool:
    lower = text.lower()
    return any(word.lower() in lower for word in words)


def _build_judgment(text: str, repeat_count: int, sectors: list[str], tickers: list[str]) -> str:
    if _contains_any(text, ["수주", "계약", "공급", "납품", "승인", "허가", "공시", "인수", "합병", "실적"]):
        base = "실제 이벤트성 뉴스로 분류. 단순 전망보다 가격 반응 가능성이 높다."
    elif _contains_any(text, ["급등", "상한가", "폭등", "돌파", "신고가"]):
        base = "가격 반응이 이미 나온 뉴스로 분류. 추격 매수보다 눌림·재돌파 확인이 우선이다."
    elif _contains_any(text, ["전망", "기대", "관심", "수혜", "관련주"]):
        base = "테마성 해석 뉴스로 분류. 거래대금 동반 여부 확인 전에는 신뢰도를 낮춰야 한다."
    elif _contains_any(text, ["하락", "급락", "악재", "조사", "제재", "소송", "유상증자"]):
        base = "리스크성 뉴스로 분류. 보유 종목이면 손절선·갭하락 가능성부터 확인해야 한다."
    else:
        base = "정보성 뉴스로 분류. 단독 매매 근거보다는 섹터 강도 확인용으로 보는 게 맞다."

    # 반복 출현은 보조 정보일 뿐, 중요도 핵심 기준으로 쓰지 않는다.
    if repeat_count >= 2:
        base += f" 동일/유사 뉴스 반복 {repeat_count}회."

    if sectors:
        base += f" 관련 섹터: {', '.join(sectors)}."
    if tickers:
        base += f" 언급 티커: {', '.join(tickers[:5])}."
    return base


def _build_trade_view(text: str, importance_score: int, repeat_count: int) -> str:
    if _contains_any(text, ["급등", "상한가", "폭등", "신고가"]):
        return "추격 금지. 5일선·VWAP·전고점 지지 확인 후 재진입 후보로만 분류."
    if _contains_any(text, ["수주", "계약", "공급", "승인", "허가", "공시", "실적"]):
        return "실제 이벤트 뉴스. 가격·거래대금 동반 시 우선 관찰. 갭상승이면 첫 눌림 확인."
    if importance_score >= 8:
        return "뉴스 강도는 높음. 실시간 가격·거래량·수급 검증 후 진입 판단."
    return "단독 매매 근거 부족. 섹터 동시 강세와 가격 반응 확인 전에는 제외."


def _build_risk(text: str) -> str:
    if _contains_any(text, ["관련주", "수혜", "기대", "전망"]):
        return "테마 과장 가능성. 실제 매출·공시·수급 확인 필요."
    if _contains_any(text, ["급등", "상한가", "폭등"]):
        return "이미 반영된 뉴스일 수 있음. 고점 추격 리스크 큼."
    if _contains_any(text, ["악재", "제재", "소송", "조사", "유상증자"]):
        return "뉴스가 추가 확산되면 하방 변동성 확대 가능."
    return "뉴스 원문만으로는 가격 반응 지속성 판단 불가. 실시간 차트 확인 필요."


def local_summarize(items: list[DedupedItem], limit: int = 15) -> list[SummaryItem]:
    """요약 후보를 만들되, 여기서 limit로 잘라 중요 뉴스를 누락시키지 않는다.

    최종 노출 개수 제한은 report.py의 중요도 스코어링 단계에서 수행한다.
    """
    summaries: list[SummaryItem] = []

    for item in items:
        sig = extract_signals(item.text, repeat_count=item.count)
        judgment = _build_judgment(item.text, item.count, sig.sectors, sig.tickers)
        trade_view = _build_trade_view(item.text, sig.importance_score, item.count)
        risk = _build_risk(item.text)
        summaries.append(
            SummaryItem(
                title=_make_title(item.text),
                body=item.text,
                channels=item.channel_names,
                categories=item.categories,
                repeat_count=item.count,
                sectors=sig.sectors,
                keywords=sig.keywords,
                tickers=sig.tickers,
                importance_score=sig.importance_score,
                judgment=judgment,
                trade_view=trade_view,
                risk=risk,
            )
        )

    return summaries


def openai_summarize_if_available(
    items: list[DedupedItem],
    api_key: str | None,
    model: str,
    limit: int = 15,
) -> list[SummaryItem]:
    # 현재 기본값은 로컬 판단형 요약입니다.
    # OPENAI_API_KEY를 연결하면 다음 단계에서 자연어 판단 요약을 붙일 수 있습니다.
    return local_summarize(items, limit=limit)
