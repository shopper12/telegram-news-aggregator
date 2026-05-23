from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
import os
import re

from rapidfuzz import fuzz

from .summarizer import SummaryItem
from .symbol_resolver import resolve_symbols, ResolvedSymbol
from .web_research import WebImpact, judge_web_impact
from .market_data import fetch_market_overview


DIVIDER = "━━━━━━━━━━━━━━"
MAX_NEWS = 5
BASE_SCORE = 72
MAX_REPORT_CHARS = 2300

STOCK_CATEGORIES = {"stock", "korea_stock", "us_stock", "kr_stock"}
BLOCK_CATEGORIES = {"crypto", "coin"}
BLOCK_SECTORS = {"bitcoin", "ethereum", "solana", "xrp", "sui", "defi", "ai_coin", "rwa"}
BLOCK_WORDS = ["btc", "eth", "비트코인", "이더리움", "코인", "업비트", "바이낸스", "온체인", "usdt", "token"]

EVENT_WORDS = ["단독", "속보", "수주", "계약", "공급", "납품", "승인", "허가", "공시", "상장", "인수", "합병", "실적", "가이던스", "증설", "투자", "MOU"]
OFFICIAL_WORDS = ["공시", "잠정", "ir", "전자공시", "거래소", "금감원", "분기보고서", "사업보고서"]
MACRO_WORDS = ["fomc", "fed", "금리", "환율", "cpi", "ppi", "고용", "국채", "달러", "유가", "관세", "수출", "수입"]
RISK_WORDS = ["급락", "악재", "제재", "조사", "소송", "유상증자", "거래정지", "부도", "파산", "감사", "리콜"]
THEME_WORDS = ["관련주", "수혜", "기대", "전망", "관심", "부각", "테마", "가능성"]
PRICE_WORDS = ["급등", "상한가", "폭등", "신고가", "장대양봉", "강세", "상승세"]
NOISE_WORDS = ["리딩", "무료방", "입장", "유료", "회원", "추천방", "수익인증", "체험", "선착순"]

TYPE_WEIGHT = {
    "공시/확정": 24,
    "이벤트": 22,
    "실적": 20,
    "리스크": 18,
    "거시": 16,
    "가격반응": 6,
    "테마": -8,
    "광고/잡음": -40,
    "정보": 0,
}

TYPE_MEANING = {
    "공시/확정": "확정성 높은 재료라 장중 수급 반응을 확인할 가치가 큼",
    "이벤트": "수주·계약·승인성 재료라 해당 섹터의 단기 관심을 높일 수 있음",
    "실적": "실적·가이던스 변화는 밸류에이션 재평가 요인",
    "리스크": "악재성 이슈라 관련 섹터의 변동성 확대 요인",
    "거시": "시장 전체 할인율·환율·수급에 영향을 줄 수 있는 변수",
    "가격반응": "이미 가격 반응이 나온 사후성 뉴스라 추격 판단에는 감점",
    "테마": "확정 사실보다 해석성 재료라 신뢰도를 낮게 봄",
    "정보": "단독 매매 근거보다는 흐름 확인용 정보",
}


@dataclass(frozen=True)
class AnalyzedNews:
    item: SummaryItem
    score: int
    news_type: str
    reasons: list[str]
    symbols: list[ResolvedSymbol]
    impact: WebImpact


@dataclass
class NewsCluster:
    key: str
    items: list[AnalyzedNews] = field(default_factory=list)

    def best(self) -> AnalyzedNews:
        return sorted(self.items, key=lambda x: x.score, reverse=True)[0]

    def score(self) -> int:
        best = self.best().score
        channel_count = len({c for n in self.items for c in n.item.channels})
        issue_count = len(self.items)
        bonus = min(10, (channel_count - 1) * 3 + (issue_count - 1) * 2)
        return min(100, best + bonus)

    def sectors(self) -> list[str]:
        counter = Counter()
        for n in self.items:
            counter.update(n.item.sectors)
        return [k for k, _ in counter.most_common(4)]

    def symbols(self) -> list[ResolvedSymbol]:
        out: list[ResolvedSymbol] = []
        seen: set[str] = set()
        for n in sorted(self.items, key=lambda x: x.score, reverse=True):
            for s in n.symbols:
                if s.ticker not in seen:
                    seen.add(s.ticker)
                    out.append(s)
        return out[:3]

    def channel_count(self) -> int:
        return len({c for n in self.items for c in n.item.channels})


def _text(item: SummaryItem) -> str:
    return f"{item.title} {item.body}"


def _blocked(item: SummaryItem) -> bool:
    cats = {c.lower() for c in item.categories}
    if cats & BLOCK_CATEGORIES:
        return True
    if set(item.sectors) & BLOCK_SECTORS:
        return True
    lower = _text(item).lower()
    return any(w in lower for w in BLOCK_WORDS)


def _stock_candidate(item: SummaryItem) -> bool:
    if _blocked(item):
        return False
    cats = {c.lower() for c in item.categories}
    return bool(cats & STOCK_CATEGORIES) or not cats


def _quote_url(sym: ResolvedSymbol) -> str:
    ticker = sym.ticker.upper()
    if ticker.endswith(".KS") or ticker.endswith(".KQ"):
        return f"https://finance.naver.com/item/main.naver?code={ticker.split('.')[0]}"
    return f"https://finance.yahoo.com/quote/{ticker}"


def _direct_symbols(item: SummaryItem) -> list[ResolvedSymbol]:
    text = _text(item)
    lower = text.lower()
    out: list[ResolvedSymbol] = []
    seen: set[str] = set()
    for sym in resolve_symbols(text, item.categories, item.tickers):
        if sym.asset_type == "crypto":
            continue
        base = sym.ticker.upper().replace(".KS", "").replace(".KQ", "")
        direct = sym.name.lower() in lower or sym.ticker.lower() in lower or base in text
        if direct and sym.ticker not in seen:
            seen.add(sym.ticker)
            out.append(sym)
    return out[:3]


def _classify(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in NOISE_WORDS):
        return "광고/잡음"
    if any(w in lower for w in RISK_WORDS):
        return "리스크"
    if any(w in lower for w in OFFICIAL_WORDS):
        return "공시/확정"
    if any(w in lower for w in ["실적", "어닝", "가이던스", "매출", "영업이익"]):
        return "실적"
    if any(w in lower for w in ["수주", "계약", "공급", "납품", "승인", "허가", "인수", "합병", "상장"]):
        return "이벤트"
    if any(w in lower for w in MACRO_WORDS):
        return "거시"
    if any(w in lower for w in PRICE_WORDS):
        return "가격반응"
    if any(w in lower for w in THEME_WORDS):
        return "테마"
    return "정보"


def _score_item(item: SummaryItem, cache: dict[str, WebImpact]) -> AnalyzedNews:
    text = _text(item)
    lower = text.lower()
    news_type = _classify(text)
    symbols = _direct_symbols(item)
    query = f"{symbols[0].name} {symbols[0].ticker} {item.title[:50]}" if symbols else item.title[:80]
    if query not in cache:
        cache[query] = judge_web_impact(query)
    impact = cache[query]

    score = 40 + TYPE_WEIGHT[news_type]
    reasons: list[str] = [news_type]

    if symbols:
        score += 8
        reasons.append("종목직접")
    if item.repeat_count >= 2:
        score += min(8, item.repeat_count * 2)
        reasons.append(f"반복{item.repeat_count}")
    if impact.impact_level == "높음":
        score += 12
        reasons.append("외부확인강")
    elif impact.impact_level == "중간":
        score += 6
        reasons.append("외부확인중")
    elif impact.impact_level == "확인부족" and news_type not in {"공시/확정", "이벤트"}:
        score -= 6
        reasons.append("외부확인부족")

    if any(w in lower for w in THEME_WORDS):
        score -= 6
        reasons.append("테마성")
    if any(w in lower for w in PRICE_WORDS) and news_type != "공시/확정":
        score -= 5
        reasons.append("사후반응")
    if any(w in lower for w in NOISE_WORDS):
        score -= 40
        reasons.append("광고제거")

    return AnalyzedNews(item=item, score=max(1, min(100, score)), news_type=news_type, reasons=reasons, symbols=symbols, impact=impact)


def _cluster_key(news: AnalyzedNews) -> str:
    if news.symbols:
        tickers = "+".join(sorted(s.ticker for s in news.symbols[:2]))
        return f"{news.news_type}:{tickers}"
    if news.item.sectors:
        return f"{news.news_type}:{news.item.sectors[0]}"
    cleaned = re.sub(r"https?://\S+", "", news.item.title.lower())
    cleaned = re.sub(r"[^0-9a-z가-힣 ]", " ", cleaned)
    words = [w for w in cleaned.split() if len(w) >= 2][:6]
    return f"{news.news_type}:{' '.join(words)}"


def _cluster(scored: list[AnalyzedNews]) -> list[NewsCluster]:
    clusters: list[NewsCluster] = []
    for news in sorted(scored, key=lambda x: x.score, reverse=True):
        key = _cluster_key(news)
        matched: NewsCluster | None = None
        for cluster in clusters:
            same_key = cluster.key == key
            similar_title = fuzz.token_set_ratio(news.item.title, cluster.best().item.title) >= 82
            share_symbol = bool({s.ticker for s in news.symbols} & {s.ticker for s in cluster.symbols()})
            if same_key or similar_title or share_symbol:
                matched = cluster
                break
        if matched:
            matched.items.append(news)
        else:
            clusters.append(NewsCluster(key=key, items=[news]))
    return clusters


def _select(items: list[SummaryItem]) -> tuple[list[NewsCluster], int, int, int, str]:
    stock = [x for x in items if _stock_candidate(x)]
    blocked = len([x for x in items if _blocked(x)])
    cache: dict[str, WebImpact] = {}
    scored = [_score_item(x, cache) for x in stock]
    scored = [x for x in scored if x.news_type != "광고/잡음" and x.score >= 45]
    clusters = _cluster(scored)

    strong = [c for c in clusters if c.score() >= BASE_SCORE]
    if not strong:
        threshold = 62
        rule = "완화"
    elif len(strong) > MAX_NEWS:
        threshold = 78
        rule = "강화"
    else:
        threshold = BASE_SCORE
        rule = "기본"

    selected = [c for c in clusters if c.score() >= threshold]
    selected = sorted(selected, key=lambda c: c.score(), reverse=True)[:MAX_NEWS]
    return selected, len(stock), blocked, threshold, rule


def _sectors(clusters: list[NewsCluster]) -> str:
    counter = Counter()
    for cluster in clusters:
        weight = max(1, cluster.score() // 20)
        for sector in cluster.sectors():
            counter[sector] += weight
    return ", ".join(f"{k}({v})" for k, v in counter.most_common(4)) or "불명확"


def _type_counts(clusters: list[NewsCluster]) -> Counter:
    counter = Counter()
    for cluster in clusters:
        counter[cluster.best().news_type] += 1
    return counter


def _market_view(clusters: list[NewsCluster]) -> str:
    if not clusters:
        return "선별 기준 통과 뉴스 없음."
    types = _type_counts(clusters)
    sector = _sectors(clusters).split(",")[0]
    top_type = types.most_common(1)[0][0]
    risk_count = types.get("리스크", 0) + types.get("거시", 0)
    positive_count = types.get("공시/확정", 0) + types.get("이벤트", 0) + types.get("실적", 0)
    if risk_count > positive_count:
        tone = "방어적 해석 우선"
    elif positive_count >= 2:
        tone = "확정성 재료 우세"
    else:
        tone = "선별 대응"
    return f"{sector} 중심. {top_type} 이슈가 핵심이며, 현재 톤은 {tone}."


def _sector_sentence(clusters: list[NewsCluster]) -> str:
    if not clusters:
        return "주도 섹터 없음."
    sector = _sectors(clusters).split(",")[0]
    top = max(clusters, key=lambda c: c.score())
    return f"{sector}가 가장 많이 반복됐고, 최상위 이슈는 {top.best().news_type} 성격이다."


def _links(symbols: list[ResolvedSymbol]) -> str:
    if not symbols:
        return "직접 언급 종목 없음"
    return ", ".join(f"{s.name}({s.ticker}) {_quote_url(s)}" for s in symbols)


def _short(text: str, limit: int) -> str:
    one = " ".join(text.replace("\n", " ").split())
    one = re.sub(r"https?://\S+", "", one).strip()
    return one if len(one) <= limit else one[:limit - 1] + "…"


def _issue_summary(cluster: NewsCluster) -> str:
    best = cluster.best()
    meaning = TYPE_MEANING.get(best.news_type, "뉴스 흐름 확인용")
    symbols = cluster.symbols()
    symbol_text = ", ".join(f"{s.name}" for s in symbols) if symbols else "직접 언급 종목 없음"
    return f"{meaning}; 관련 표기는 {symbol_text} 기준."


def _issue_impact(cluster: NewsCluster) -> str:
    best = cluster.best()
    sectors = ", ".join(cluster.sectors()[:3]) or "불명확"
    external = best.impact.impact_level
    channel_count = cluster.channel_count()
    if best.news_type in {"공시/확정", "이벤트", "실적"}:
        return f"{sectors} 섹터 관심도를 높일 수 있음. 외부확인={external}, 채널={channel_count}."
    if best.news_type in {"리스크", "거시"}:
        return f"{sectors} 관련 변동성 확대 요인. 외부확인={external}, 채널={channel_count}."
    if best.news_type == "가격반응":
        return f"이미 가격 반응이 포함된 뉴스라 신규 재료성은 낮게 평가. 외부확인={external}."
    if best.news_type == "테마":
        return f"테마성 해석이므로 확정 뉴스보다 낮은 비중으로 처리. 외부확인={external}."
    return f"섹터 흐름 확인용. 외부확인={external}, 채널={channel_count}."


def _why(cluster: NewsCluster) -> str:
    best = cluster.best()
    sectors = ", ".join(cluster.sectors()[:3]) or "불명확"
    return f"유형={best.news_type}, 섹터={sectors}, 근거={', '.join(best.reasons[:3])}"


def _header(kind: str) -> str:
    if kind == "preopen_0850":
        return "🌅 08:50 개장 전 주식 브리핑"
    if kind == "afterclose_1530":
        return "🏁 15:30 장후 주식 브리핑"
    return "📰 주식 뉴스 브리핑"


def _overview() -> str:
    items = [x for x in fetch_market_overview() if not x.startswith(("BTC", "ETH"))]
    return " / ".join(items[:5]) if items else "시장 데이터 확인 실패"


def _quality_note(rule: str, source_count: int, stock_count: int, blocked: int, clusters: list[NewsCluster]) -> str:
    type_text = ", ".join(f"{k}{v}" for k, v in _type_counts(clusters).most_common(4)) or "없음"
    return f"검증: 로컬엔진 · {rule} · 원문 {source_count} · 후보 {stock_count} · 제외 {blocked} · 유형 {type_text}"


def build_markdown_report(summaries: list[SummaryItem], hours: int, timezone_name: str = "Asia/Seoul") -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    kind = os.getenv("BRIEFING_KIND", "regular")
    clusters, stock_count, blocked, threshold, rule = _select(summaries)
    overview = _overview()

    lines: list[str] = [
        _header(kind),
        DIVIDER,
        f"{now:%m/%d %H:%M KST} | 최근 {hours}h | 이슈 {len(clusters)}개 | 기준 {threshold}",
        f"시장: {overview}",
        f"시황: {_market_view(clusters)}",
        f"주요 섹터: {_sector_sentence(clusters)}",
        "",
    ]

    if not clusters:
        lines.append("주요 뉴스: 기준 통과 없음")
    else:
        lines.append("📌 핵심 이슈")
        for idx, cluster in enumerate(clusters, 1):
            best = cluster.best()
            lines.append(f"{idx}) [{cluster.score()}] {_short(best.item.title, 72)}")
            lines.append(f"   요지: {_issue_summary(cluster)}")
            lines.append(f"   영향: {_issue_impact(cluster)}")
            lines.append(f"   판단: {_why(cluster)}")
            lines.append(f"   관련: {_links(cluster.symbols())}")

    lines.append("")
    lines.append(_quality_note(rule, len(summaries), stock_count, blocked, clusters))
    report = "\n".join(lines)
    return report[:MAX_REPORT_CHARS - 20] + "\n… 이하 생략" if len(report) > MAX_REPORT_CHARS else report
