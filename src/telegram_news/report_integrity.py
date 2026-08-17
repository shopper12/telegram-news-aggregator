from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
import os
import re
from types import MappingProxyType
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")
STRATEGY_NEWS_LOOKBACK_HOURS = int(os.getenv("STRATEGY_NEWS_LOOKBACK_HOURS", "24"))
PROXY_MAX_DIVERGENCE_PCT = float(os.getenv("HYPERLIQUID_PROXY_MAX_DIVERGENCE_PCT", "2.0"))

INDEX_LABELS = {
    "dow_change_pct": "다우",
    "sp500_change_pct": "S&P500",
    "nasdaq_change_pct": "Nasdaq",
    "russell2000_change_pct": "러셀2000",
    "sox_change_pct": "필라델피아 반도체",
    "kospi_change_pct": "KOSPI",
    "kosdaq_change_pct": "KOSDAQ",
}

US_INDEX_FIELDS = (
    "dow_change_pct",
    "sp500_change_pct",
    "nasdaq_change_pct",
    "russell2000_change_pct",
    "sox_change_pct",
)
KR_INDEX_FIELDS = (
    "kospi_change_pct",
    "kosdaq_change_pct",
    "sp500_change_pct",
    "nasdaq_change_pct",
)
GLOBAL_INDEX_FIELDS = (
    "sp500_change_pct",
    "nasdaq_change_pct",
    "russell2000_change_pct",
    "sox_change_pct",
)
REPORT_INDEX_WHITELIST = {
    "us_close": US_INDEX_FIELDS,
    "strategy_morning": US_INDEX_FIELDS,
    "strategy_evening": US_INDEX_FIELDS,
    "us_premarket_before": US_INDEX_FIELDS,
    "us_premarket_after": US_INDEX_FIELDS,
    "kr_premarket": KR_INDEX_FIELDS,
    "premarket": KR_INDEX_FIELDS,
    "intraday": KR_INDEX_FIELDS,
    "kr_aftermarket": KR_INDEX_FIELDS,
    "aftermarket": KR_INDEX_FIELDS,
    "overnight": GLOBAL_INDEX_FIELDS,
    "regular": GLOBAL_INDEX_FIELDS,
    "manual": GLOBAL_INDEX_FIELDS,
}

REPORT_SNAPSHOT_ASSETS = {
    "^DJI",
    "^GSPC",
    "^IXIC",
    "^RUT",
    "^SOX",
    "^TYX",
    "CL=F",
}


@dataclass(frozen=True)
class MarketSnapshot:
    report_kind: str
    captured_at: datetime
    market_context: Mapping[str, Any]
    global_snapshot: Mapping[str, Any]
    index_values: Mapping[str, float | None]
    allowed_index_fields: tuple[str, ...]

    def index_value(self, field: str) -> float | None:
        self.assert_allowed(field)
        value = self.index_values.get(field)
        return float(value) if isinstance(value, (int, float)) else None

    def assert_allowed(self, field: str) -> None:
        if field not in self.allowed_index_fields:
            raise ValueError(
                f"index field {field!r} is not allowed for report kind {self.report_kind!r}; "
                f"allowed={self.allowed_index_fields}"
            )


@dataclass(frozen=True)
class NewsPool:
    summaries: tuple[Any, ...]
    selected: tuple[Any, ...]
    displayed: tuple[Any, ...]
    suppressed_count: int
    stock_count: int
    blocked_count: int
    pre_gate_count: int
    rule: str

    @property
    def displayed_count(self) -> int:
        return len(self.displayed)


@dataclass(frozen=True)
class ReportRunContext:
    market: MarketSnapshot
    news: NewsPool
    hours: int
    timezone_name: str


_ACTIVE_CONTEXT: ContextVar[ReportRunContext | None] = ContextVar("telegram_news_report_context", default=None)
_INSTALLED = False
_DISPATCH_HOOKED = False
_ORIGINALS: dict[str, Any] = {}
_PROXY_CHECKS: dict[str, dict[str, Any]] = {}


def current_context() -> ReportRunContext | None:
    return _ACTIVE_CONTEXT.get()


def current_market_snapshot() -> MarketSnapshot | None:
    context = current_context()
    return context.market if context else None


def current_news_pool() -> NewsPool | None:
    context = current_context()
    return context.news if context else None


@contextmanager
def activate_context(context: ReportRunContext):
    token = _ACTIVE_CONTEXT.set(context)
    try:
        yield context
    finally:
        _ACTIVE_CONTEXT.reset(token)


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except Exception:
        return None


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    # Snapshot ownership is enforced by the frozen dataclass and private copies.
    # Nested dictionaries remain normal mappings because existing formatters use
    # dict-style access throughout; callers receive no mutation API from here.
    return MappingProxyType(dict(value))


def _asset_change(snapshot: Mapping[str, Any], ticker: str) -> float | None:
    assets = snapshot.get("assets") or {}
    item = assets.get(ticker) or {}
    return _safe_float(item.get("change_pct"))


def _kind_fields(kind: str) -> tuple[str, ...]:
    normalized = str(kind or "regular").strip().lower()
    return tuple(REPORT_INDEX_WHITELIST.get(normalized, GLOBAL_INDEX_FIELDS))


def _index_values(kind: str, market_context: Mapping[str, Any], global_snapshot: Mapping[str, Any]) -> dict[str, float | None]:
    values = {
        "dow_change_pct": _asset_change(global_snapshot, "^DJI"),
        "sp500_change_pct": _asset_change(global_snapshot, "^GSPC"),
        "nasdaq_change_pct": _asset_change(global_snapshot, "^IXIC"),
        "russell2000_change_pct": _asset_change(global_snapshot, "^RUT"),
        "sox_change_pct": _asset_change(global_snapshot, "^SOX"),
        "kospi_change_pct": _safe_float(market_context.get("kospi_change_pct")),
        "kosdaq_change_pct": _safe_float(market_context.get("kosdaq_change_pct")),
    }
    # Korean reports may use the base market context when the global quote batch
    # did not return an overseas index. US reports never do this: their header and
    # axis must come from the same global snapshot values.
    normalized = str(kind or "").lower()
    if normalized not in {"us_close", "strategy_morning", "strategy_evening", "us_premarket_before", "us_premarket_after"}:
        if values["sp500_change_pct"] is None:
            values["sp500_change_pct"] = _safe_float(market_context.get("sp500_change_pct"))
        if values["nasdaq_change_pct"] is None:
            values["nasdaq_change_pct"] = _safe_float(market_context.get("nasdaq_change_pct"))
    return values


def build_market_snapshot(
    *,
    kind: str,
    market_context: Mapping[str, Any] | None,
    global_snapshot: Mapping[str, Any] | None,
    captured_at: datetime | None = None,
) -> MarketSnapshot:
    market = dict(market_context or {})
    global_data = dict(global_snapshot or {})
    return MarketSnapshot(
        report_kind=str(kind or "regular").lower(),
        captured_at=captured_at or datetime.now(KST),
        market_context=_freeze_mapping(market),
        global_snapshot=_freeze_mapping(global_data),
        index_values=_freeze_mapping(_index_values(kind, market, global_data)),
        allowed_index_fields=_kind_fields(kind),
    )


def _collect_atomic_global_snapshot() -> dict[str, Any]:
    from . import global_market_tracker

    snapshot = dict(_ORIGINALS["collect_global_snapshot"]())
    assets = dict(snapshot.get("assets") or {})
    missing = [ticker for ticker in REPORT_SNAPSHOT_ASSETS if ticker not in assets]
    if missing:
        with ThreadPoolExecutor(max_workers=min(7, len(missing)), thread_name_prefix="atomic-market") as pool:
            futures = {pool.submit(global_market_tracker.fetch_asset_snapshot, ticker): ticker for ticker in missing}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    item = future.result()
                except Exception as exc:
                    item = {"ticker": ticker, "price": None, "change_pct": None, "error": f"{type(exc).__name__}: {exc}"}
                assets[ticker] = item
    snapshot["assets"] = assets
    snapshot["data_quality"] = sum(1 for item in assets.values() if isinstance(item, dict) and item.get("price") is not None)
    snapshot["requested_assets"] = len(assets)
    snapshot["atomic_snapshot"] = True
    snapshot["atomic_captured_at"] = datetime.now(KST).isoformat(timespec="seconds")
    return snapshot


def _build_news_pool(summaries: Iterable[Any], now: datetime) -> NewsPool:
    from . import strict_report_v2

    selected, stock_count, blocked, rule, pre_gate_count = _ORIGINALS["select_strict"](list(summaries))
    raw_display = strict_report_v2._drop_noise(list(selected))
    displayed, suppressed = _ORIGINALS["suppress_recent_duplicates"](raw_display, now)
    return NewsPool(
        summaries=tuple(summaries),
        selected=tuple(selected),
        displayed=tuple(displayed),
        suppressed_count=int(suppressed),
        stock_count=int(stock_count),
        blocked_count=int(blocked),
        pre_gate_count=int(pre_gate_count),
        rule=str(rule),
    )


def _context_select_strict(items: list[Any]):
    pool = current_news_pool()
    if pool is None:
        return _ORIGINALS["select_strict"](items)
    return (
        list(pool.selected),
        pool.stock_count,
        pool.blocked_count,
        pool.rule,
        pool.pre_gate_count,
    )


def _context_suppress_recent_duplicates(clusters: list[Any], now: datetime):
    pool = current_news_pool()
    if pool is None:
        return _ORIGINALS["suppress_recent_duplicates"](clusters, now)
    return list(pool.displayed), pool.suppressed_count


def _context_market_context():
    snapshot = current_market_snapshot()
    if snapshot is not None:
        return snapshot.market_context
    return _ORIGINALS["get_market_context"]()


def _context_global_snapshot():
    snapshot = current_market_snapshot()
    if snapshot is not None:
        return snapshot.global_snapshot
    return _ORIGINALS["cached_global_snapshot"]()


def _no_refetch_note_assets(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    result = snapshot or {}
    assets = result.get("assets") or {}
    missing = [ticker for ticker in REPORT_SNAPSHOT_ASSETS if _safe_float((assets.get(ticker) or {}).get("price")) is None]
    if missing:
        print(f"[report-integrity] atomic snapshot missing note assets; no refetch: {','.join(sorted(missing))}")
    return result


def _index_bucket(value: Any) -> int | None:
    number = _safe_float(value)
    if number is None:
        return None
    if number <= -5:
        return -3
    if number <= -2:
        return -1
    if number < 0:
        return 0
    if number >= 5:
        return 3
    if number >= 2:
        return 2
    if number > 0:
        return 1
    return 0


def atomic_index_axis(
    context: Mapping[str, Any] | None = None,
    *,
    market_snapshot: MarketSnapshot | None = None,
    requested_fields: Iterable[str] | None = None,
) -> tuple[float, str, int]:
    snapshot = market_snapshot or current_market_snapshot()
    if snapshot is None:
        # Direct library calls outside a report retain a deterministic legacy view.
        values = []
        parts = []
        for field in KR_INDEX_FIELDS:
            value = (context or {}).get(field)
            bucket = _index_bucket(value)
            if bucket is None:
                continue
            values.append(bucket)
            parts.append(f"{INDEX_LABELS[field]} {float(value):+.2f}%→{bucket:+d}")
        axis = sum(values) / len(values) if values else 0.0
        return axis, " / ".join(parts) if parts else "지수 데이터 미확인→0", len(values)

    fields = tuple(requested_fields or snapshot.allowed_index_fields)
    for field in fields:
        snapshot.assert_allowed(field)

    values: list[int] = []
    parts: list[str] = []
    for field in fields:
        value = snapshot.index_value(field)
        bucket = _index_bucket(value)
        if bucket is None:
            continue
        values.append(bucket)
        parts.append(f"{INDEX_LABELS[field]} {value:+.2f}%→{bucket:+d}")
    axis = sum(values) / len(values) if values else 0.0
    return axis, " / ".join(parts) if parts else "지수 데이터 미확인→0", len(values)


def _arrow(value: float | None) -> str:
    if value is None:
        return "· 확인불가"
    return f"▲ {value:+.2f}%" if value > 0 else f"▼ {value:+.2f}%" if value < 0 else "― +0.00%"


def snapshot_index_lines(
    note_name: str,
    market_context: Mapping[str, Any] | None,
    snapshot: Mapping[str, Any],
) -> list[str]:
    market_snapshot = current_market_snapshot()
    if market_snapshot is None:
        return _ORIGINALS["index_lines"](note_name, market_context, snapshot)

    fields = market_snapshot.allowed_index_fields
    values = {field: market_snapshot.index_value(field) for field in fields}
    if fields == US_INDEX_FIELDS:
        return [
            f"　다우 {_arrow(values.get('dow_change_pct'))}　나스닥 {_arrow(values.get('nasdaq_change_pct'))}",
            f"　S&P500 {_arrow(values.get('sp500_change_pct'))}　러셀2000 {_arrow(values.get('russell2000_change_pct'))}",
            f"　필라델피아 반도체 {_arrow(values.get('sox_change_pct'))}",
        ]

    ordered = [field for field in fields if field in values]
    pairs: list[str] = []
    for field in ordered:
        pairs.append(f"{INDEX_LABELS[field]} {_arrow(values.get(field))}")
    lines = ["　" + "　".join(pairs[index:index + 2]) for index in range(0, len(pairs), 2)]
    usdkrw = _safe_float(market_snapshot.market_context.get("usd_krw"))
    if usdkrw is not None:
        lines.append(f"　USD/KRW {usdkrw:,.1f}")
    return lines


def _summary_news_with_urls(summaries: list[Any], now: datetime) -> list[dict[str, Any]]:
    from hashlib import sha1
    from . import adaptive_strategy

    events: list[dict[str, Any]] = []
    timestamp = now.isoformat(timespec="seconds")
    for item in summaries[:200]:
        title = str(getattr(item, "title", "") or "").strip()
        body = str(getattr(item, "body", "") or "").strip()
        if not title and not body:
            continue
        judgment = str(getattr(item, "judgment", "") or "").strip()
        risk = str(getattr(item, "risk", "") or "").strip()
        sectors = [str(value) for value in (getattr(item, "sectors", None) or []) if str(value).strip()]
        keywords = [str(value) for value in (getattr(item, "keywords", None) or []) if str(value).strip()]
        tickers = [str(value) for value in (getattr(item, "tickers", None) or []) if str(value).strip()]
        urls = [str(value).strip() for value in (getattr(item, "source_urls", None) or []) if str(value).startswith(("http://", "https://"))]
        score = int(getattr(item, "importance_score", 0) or 0)
        repeat_count = max(1, int(getattr(item, "repeat_count", 1) or 1))
        dates = list(getattr(item, "message_dates", None) or [])
        text = " ".join(part for part in (title, body, judgment, risk) if part)
        sentiment = max(-3, min(3, adaptive_strategy._hits(text, adaptive_strategy.POSITIVE_WORDS) - adaptive_strategy._hits(text, adaptive_strategy.NEGATIVE_WORDS)))
        signature = sha1(f"{title}|{','.join(sectors[:4])}|{','.join(tickers[:4])}".encode("utf-8")).hexdigest()
        events.append(
            {
                "signature": signature,
                "title": title or body[:120],
                "sectors": sectors[:6],
                "keywords": keywords[:10],
                "tickers": tickers[:8],
                "source_urls": list(dict.fromkeys(urls))[:5],
                "materiality": max(0, min(100, score)),
                "sentiment": sentiment,
                "first_seen": str(min(dates) if dates else timestamp),
                "last_seen": str(max(dates) if dates else timestamp),
                "count": repeat_count,
            }
        )
    return events


def _selected_news_with_urls(selected: list[Any], now: datetime) -> list[dict[str, Any]]:
    from hashlib import sha1
    from . import adaptive_strategy, strict_report_v2
    from .strict_quality import materiality_score

    events = []
    timestamp = now.isoformat(timespec="seconds")
    for cluster in selected[:50]:
        try:
            title = strict_report_v2._display_title(cluster, 120)
            text = strict_report_v2._cluster_text(cluster)
            sectors = [str(value) for value in (cluster.sectors() or []) if str(value).strip()]
            score = int(materiality_score(cluster))
            url = str(strict_report_v2._source_url(cluster) or "").strip()
        except Exception:
            continue
        sentiment = max(-3, min(3, adaptive_strategy._hits(text, adaptive_strategy.POSITIVE_WORDS) - adaptive_strategy._hits(text, adaptive_strategy.NEGATIVE_WORDS)))
        signature = sha1(f"{title}|{','.join(sectors[:4])}".encode("utf-8")).hexdigest()
        events.append(
            {
                "signature": signature,
                "title": title,
                "sectors": sectors[:6],
                "keywords": [],
                "tickers": [],
                "source_urls": [url] if url.startswith(("http://", "https://")) else [],
                "materiality": score,
                "sentiment": sentiment,
                "first_seen": timestamp,
                "last_seen": timestamp,
                "count": 1,
            }
        )
    return events


def _news_score_with_evidence(meta: dict[str, Any], memory: dict[str, Any], now: datetime) -> tuple[float, list[dict[str, Any]]]:
    keywords = [str(value).lower() for value in meta.get("keywords", [])]
    cutoff = now - timedelta(hours=STRATEGY_NEWS_LOOKBACK_HOURS)
    total = 0.0
    reasons: list[tuple[float, dict[str, Any]]] = []
    for event in memory.get("events", []):
        try:
            seen = datetime.fromisoformat(str(event.get("last_seen")).replace("Z", "+00:00"))
            if seen.tzinfo is None:
                seen = seen.replace(tzinfo=KST)
            seen = seen.astimezone(KST)
        except Exception:
            continue
        if seen < cutoff:
            continue
        urls = [str(value) for value in (event.get("source_urls") or []) if str(value).startswith(("http://", "https://"))]
        if not urls:
            # A news signal that the user cannot independently verify is not
            # permitted to influence the strategy's news component.
            continue
        haystack = " ".join(
            [
                str(event.get("title") or ""),
                " ".join(event.get("sectors") or []),
                " ".join(event.get("keywords") or []),
                " ".join(event.get("tickers") or []),
            ]
        ).lower()
        if not any(keyword in haystack for keyword in keywords if keyword):
            continue
        recency = max(0.2, 1.0 - (now - seen).total_seconds() / (STRATEGY_NEWS_LOOKBACK_HOURS * 3600.0))
        repeat_weight = min(2.0, 1.0 + max(0, int(event.get("count") or 1) - 1) * 0.1)
        contribution = float(event.get("sentiment") or 0) * float(event.get("materiality") or 0) / 100.0 * recency * repeat_weight
        total += contribution
        reasons.append(
            (
                abs(contribution),
                {"title": str(event.get("title") or ""), "urls": list(dict.fromkeys(urls))[:2], "last_seen": str(event.get("last_seen") or "")},
            )
        )
    reasons.sort(key=lambda item: item[0], reverse=True)
    return max(-4.0, min(4.0, total)), [item for _, item in reasons[:2] if item.get("title")]


def _candidates_with_provenance(snapshot: dict[str, Any], memory: dict[str, Any], state: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    from . import adaptive_strategy
    from .global_market_tracker import TRADE_ASSETS

    weights = state.get("weights") or {}
    out = []
    assets = snapshot.get("assets") or {}
    for ticker, meta in TRADE_ASSETS.items():
        item = assets.get(ticker) or {}
        if item.get("proxy_divergence_exceeded"):
            print(
                "[strategy-proxy] excluded "
                f"ticker={ticker} divergence={float(item.get('proxy_divergence_pct') or 0):.3f}% "
                f"threshold={PROXY_MAX_DIVERGENCE_PCT:.3f}%"
            )
            continue
        price = _safe_float(item.get("price"))
        if price is None:
            continue
        news, evidence = _news_score_with_evidence(meta, memory, now)
        components = {
            "momentum": adaptive_strategy._momentum(item),
            "regime": adaptive_strategy._regime(meta["group"], str(snapshot.get("regime"))),
            "news": news,
            "defensive": adaptive_strategy._defensive(meta["group"], snapshot),
        }
        total = sum(float(weights.get(name, 1.0)) * value for name, value in components.items())
        out.append(
            {
                "ticker": ticker,
                "asset": meta["name"],
                "group": meta["group"],
                "price": price,
                "volatility": _safe_float(item.get("volatility_20d")) or 2.0,
                "components": components,
                "score": round(total, 3),
                "news_evidence": evidence,
                "price_source": str(item.get("price_source") or "Yahoo Finance regular-session snapshot"),
                "price_warning": str(item.get("price_warning") or ""),
                "price_is_derivative_proxy": bool(item.get("price_is_derivative_proxy")),
                "proxy_divergence_pct": _safe_float(item.get("proxy_divergence_pct")),
            }
        )
    return sorted(out, key=lambda item: item["score"], reverse=True)


def _recommendation_with_provenance(candidate: dict[str, Any], slot: str, state: dict[str, Any], now: datetime) -> dict[str, Any]:
    from hashlib import sha1

    price = float(candidate["price"])
    stop_pct = max(2.0, min(8.0, float(candidate["volatility"]) * 1.6))
    target_pct = max(4.0, min(16.0, stop_pct * 2.0))
    reason = (
        f"글로벌 레짐 {candidate['components']['regime']:+.2f}, "
        f"모멘텀 {candidate['components']['momentum']:+.2f}, 뉴스 {candidate['components']['news']:+.2f}"
    )
    evidence = list(candidate.get("news_evidence") or [])
    if evidence:
        reason += " / 연결 뉴스: " + "; ".join(str(item.get("title") or "") for item in evidence)
    if candidate.get("price_is_derivative_proxy"):
        reason += " / 가격소스: 파생 프록시(실거래가 아님)"
    return {
        "id": sha1(f"{slot}|{candidate['ticker']}|{now.isoformat(timespec='minutes')}".encode("utf-8")).hexdigest()[:16],
        "created_at": now.isoformat(timespec="seconds"),
        "slot": slot,
        "ticker": candidate["ticker"],
        "asset": candidate["asset"],
        "direction": "LONG",
        "entry_price": round(price, 6),
        "entry_zone": [round(price * 0.995, 6), round(price * 1.005, 6)],
        "stop_price": round(price * (1 - stop_pct / 100), 6),
        "target_price": round(price * (1 + target_pct / 100), 6),
        "score": candidate["score"],
        "components": candidate["components"],
        "component_text": ", ".join(f"{name} {value:+.2f}" for name, value in candidate["components"].items()),
        "weights": dict(state.get("weights") or {}),
        "reason": reason,
        "news_evidence": evidence,
        "price_source": candidate.get("price_source"),
        "price_warning": candidate.get("price_warning"),
        "price_is_derivative_proxy": bool(candidate.get("price_is_derivative_proxy")),
        "proxy_divergence_pct": candidate.get("proxy_divergence_pct"),
        "status": "open",
        "evaluations": {},
        "learned_24h": False,
    }


def _format_price(value: Any, *, proxy: bool = False) -> str:
    number = _safe_float(value)
    if number is None:
        text = "확인불가"
    elif abs(number) >= 1000:
        text = f"{number:,.0f}"
    elif abs(number) >= 10:
        text = f"{number:,.2f}"
    else:
        text = f"{number:.4f}"
    return f"{text} (프록시, 실거래가 아님)" if proxy and number is not None else text


def _performance_text(state: dict[str, Any]) -> str:
    from .strategy_learning import MODEL_COMPONENTS

    stats = state.get("stats") or {}
    count = int(stats.get("evaluated_24h") or 0)
    wins = int(stats.get("wins_24h") or 0)
    win_rate = wins / count * 100.0 if count else 0.0
    weights = state.get("weights") or {}
    weight_text = ", ".join(f"{name} {float(weights.get(name, 1.0)):.2f}" for name in MODEL_COMPONENTS)
    payoff = stats.get("payoff_ratio_24h")
    payoff_text = f"{float(payoff):.2f}" if isinstance(payoff, (int, float)) else "확인불가"
    active = bool(stats.get("weight_adjustment_active"))
    return (
        f"24시간 검증 {count}건 · 승률 {win_rate:.1f}% · 평균 {float(stats.get('average_return_24h_pct') or 0):+.2f}% · "
        f"평균승리 {float(stats.get('average_win_24h_pct') or 0):+.2f}% · 평균손실 {float(stats.get('average_loss_24h_pct') or 0):+.2f}% · "
        f"손익비 {payoff_text} · MDD -{float(stats.get('max_drawdown_24h_pct') or 0):.2f}% · "
        f"가중치 {'자동조정' if active else '고정(표본부족)'} {weight_text}"
    )


def _build_strategy_section(
    snapshot: dict[str, Any],
    memory: dict[str, Any],
    state: dict[str, Any],
    ledger: dict[str, Any],
    slot: str | None,
    recommendations: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    learned: int,
) -> str:
    from .strategy_learning import MEMORY_RETENTION_HOURS

    lines = [
        "🌐 글로벌 시황·수급 프록시",
        f"  • 레짐: {snapshot.get('regime_label', '확인불가')} | 점수 {float(snapshot.get('regime_score') or 0):+.2f}",
        f"  • 지역: {snapshot.get('regions') or '확인불가'}",
        f"  • 자금 흐름 프록시: {snapshot.get('flow_proxy') or '확인불가'}",
        f"  • 데이터: {snapshot.get('data_quality', 0)}/{snapshot.get('requested_assets', 0)} 자산 확인",
        "  • 주의: ETF·지수·금리·변동성 가격을 이용한 수급 프록시이며 실시간 순매수 원자료와 동일하지 않음",
        "",
        "🧠 지속학습 상태",
        f"  • 뉴스 메모리: 최근 {MEMORY_RETENTION_HOURS}시간 {len(memory.get('events', []))}개 이슈",
        f"  • 전략 원장: 진행 {sum(item.get('status') == 'open' for item in ledger.get('recommendations', []))}건 · 이번 평가 {len(evaluations)}건 · 이번 학습 {learned}건",
        f"  • 누적 성과: {_performance_text(state)}",
    ]
    if not slot:
        lines.append("  • 정식 전략은 매일 07:00 아침과 22:30 저녁 실행에서 생성")
        return "\n".join(lines)

    label = "아침" if slot == "morning" else "저녁" if slot == "evening" else "수동"
    lines += ["", f"🎯 {label} 글로벌 매매전략"]
    pool = current_news_pool()
    uses_memory = bool(recommendations and any(item.get("news_evidence") for item in recommendations))
    if pool is not None and pool.displayed_count == 0 and uses_memory:
        lines.append(
            f"※ 아래 전략은 최근 {STRATEGY_NEWS_LOOKBACK_HOURS}시간 누적 메모리 기준이며, "
            "상단 신규 이슈(0건)와는 별개 데이터입니다."
        )
    if not recommendations:
        lines += ["  • 판정: 신규 진입 보류", "  • 이유: 데이터 부족 또는 최소 점수 미달", "  • 대응: 현금 비중 유지 후 다음 30분 재평가"]

    for index, item in enumerate(recommendations, 1):
        proxy = bool(item.get("price_is_derivative_proxy"))
        zone = item["entry_zone"]
        lines += [
            f"{index}) {item['asset']}({item['ticker']}) LONG | 점수 {float(item['score']):+.2f}",
            f"  • 진입구간: {_format_price(zone[0], proxy=proxy)} ~ {_format_price(zone[1], proxy=proxy)} | 기준가 {_format_price(item['entry_price'], proxy=proxy)}",
            f"  • 손절/무효: {_format_price(item['stop_price'], proxy=proxy)} | 목표: {_format_price(item['target_price'], proxy=proxy)}",
            f"  • 구성점수: {item['component_text']}",
            f"  • 근거: {item['reason']}",
        ]
        if proxy:
            divergence = item.get("proxy_divergence_pct")
            divergence_text = f" · 실제가 대비 괴리 {float(divergence):.2f}%" if isinstance(divergence, (int, float)) else ""
            lines.append(f"  • 가격주의: 파생 프록시, 실제 거래소 체결가 아님{divergence_text}")
        for evidence in item.get("news_evidence") or []:
            title = str(evidence.get("title") or "근거 뉴스")
            urls = [str(url) for url in evidence.get("urls") or [] if str(url).startswith(("http://", "https://"))]
            for url in urls:
                lines.append(f"  • 근거뉴스: {title} · {url}")

    lines += [
        "  • 검증: 6시간·24시간·72시간 가격을 기록하고, 200건 이상부터 24시간 결과로 가중치를 제한 조정",
        "  • 주의: 체결·세금·슬리피지·개인 위험한도를 반영하지 않은 자동 생성 전략",
    ]
    return "\n".join(lines)


def _run_adaptive_cycle_shared(original, selected, kind, now=None, snapshot=None, all_summaries=None):
    context = current_context()
    if context is None:
        return original(selected, kind, now=now, snapshot=snapshot, all_summaries=all_summaries)
    return original(
        list(context.news.selected),
        kind,
        now=now,
        snapshot=context.market.global_snapshot,
        all_summaries=list(context.news.summaries),
    )


def _safe_fetch_hyperliquid_proxy(ticker: str, reference_price: float | None):
    from . import continuous_quote_fallback as cq

    if not cq._eligible_equity_ticker(ticker):
        return None
    reference = cq._safe_float(reference_price)
    if reference is None or reference <= 0:
        return None
    candidate = cq._refresh_hyperliquid_markets().get(cq._symbol_key(ticker))
    if candidate is None:
        return None
    divergence = abs(candidate.price / reference - 1.0) * 100.0
    accepted = divergence <= PROXY_MAX_DIVERGENCE_PCT
    _PROXY_CHECKS[str(ticker).upper()] = {
        "reference": reference,
        "proxy": float(candidate.price),
        "divergence_pct": divergence,
        "accepted": accepted,
    }
    print(
        "[proxy-divergence] "
        f"ticker={ticker} official={reference:.6f} proxy={candidate.price:.6f} "
        f"divergence={divergence:.3f}% threshold={PROXY_MAX_DIVERGENCE_PCT:.3f}% "
        f"accepted={str(accepted).lower()}"
    )
    return candidate if accepted else None


def _resolve_snapshot_item_with_divergence(ticker: str, item: dict[str, Any], quote_type: Any) -> dict[str, Any]:
    _PROXY_CHECKS.pop(str(ticker).upper(), None)
    out = _ORIGINALS["resolve_snapshot_item"](ticker, item, quote_type)
    check = _PROXY_CHECKS.get(str(ticker).upper())
    if check:
        out = dict(out)
        out["proxy_divergence_pct"] = round(float(check["divergence_pct"]), 4)
        out["proxy_divergence_exceeded"] = not bool(check["accepted"])
    return out


def _install_proxy_guards() -> None:
    from . import continuous_quote_fallback as cq

    cq.MAX_PROXY_DIVERGENCE_PCT = PROXY_MAX_DIVERGENCE_PCT
    _ORIGINALS.setdefault("resolve_snapshot_item", cq._resolve_snapshot_item)
    cq.fetch_hyperliquid_proxy = _safe_fetch_hyperliquid_proxy
    cq._resolve_snapshot_item = _resolve_snapshot_item_with_divergence


def _install_strategy_guards() -> None:
    from . import adaptive_strategy

    _ORIGINALS.setdefault("run_adaptive_cycle", adaptive_strategy.run_adaptive_cycle)
    original_cycle = _ORIGINALS["run_adaptive_cycle"]

    def shared_cycle(selected, kind, now=None, snapshot=None, all_summaries=None):
        return _run_adaptive_cycle_shared(original_cycle, selected, kind, now=now, snapshot=snapshot, all_summaries=all_summaries)

    adaptive_strategy._summary_news = _summary_news_with_urls
    adaptive_strategy._selected_news = _selected_news_with_urls
    adaptive_strategy._news_score = _news_score_with_evidence
    adaptive_strategy._candidates = _candidates_with_provenance
    adaptive_strategy._recommendation = _recommendation_with_provenance
    adaptive_strategy.build_strategy_section = _build_strategy_section
    adaptive_strategy.collect_global_snapshot = _context_global_snapshot
    adaptive_strategy.run_adaptive_cycle = shared_cycle


def _install_market_guards() -> None:
    from . import consistency_rules, global_market_tracker, market_note_formatter, strict_report, strict_report_v2

    _ORIGINALS.setdefault("collect_global_snapshot", global_market_tracker.collect_global_snapshot)
    _ORIGINALS.setdefault("cached_global_snapshot", consistency_rules._cached_global_snapshot)
    _ORIGINALS.setdefault("get_market_context", strict_report_v2.get_market_context)
    _ORIGINALS.setdefault("select_strict", strict_report._select_strict)
    _ORIGINALS.setdefault("suppress_recent_duplicates", strict_report_v2._suppress_recent_duplicates)
    _ORIGINALS.setdefault("ensure_note_assets", market_note_formatter._ensure_note_assets)
    _ORIGINALS.setdefault("index_lines", market_note_formatter._index_lines)

    strict_report._select_strict = _context_select_strict
    strict_report_v2.get_market_context = _context_market_context
    strict_report_v2._suppress_recent_duplicates = _context_suppress_recent_duplicates
    consistency_rules._cached_global_snapshot = _context_global_snapshot
    consistency_rules._index_axis = atomic_index_axis
    market_note_formatter._ensure_note_assets = _no_refetch_note_assets
    market_note_formatter._index_lines = snapshot_index_lines


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from . import app, consistency_rules, strict_report_v2

    _install_proxy_guards()
    _install_strategy_guards()
    _install_market_guards()

    current = strict_report_v2.build_markdown_report
    if getattr(current, "_report_integrity_installed", False):
        _INSTALLED = True
        return
    _ORIGINALS["build_markdown_report"] = current

    def wrapped(summaries, hours: int, timezone_name: str = "Asia/Seoul") -> str:
        kind = str(os.getenv("BRIEFING_KIND", "regular") or "regular").lower()
        now = datetime.now(ZoneInfo(timezone_name))
        market_context = _ORIGINALS["get_market_context"]() or {}
        global_snapshot = _collect_atomic_global_snapshot()
        market = build_market_snapshot(
            kind=kind,
            market_context=market_context,
            global_snapshot=global_snapshot,
            captured_at=now,
        )
        news = _build_news_pool(list(summaries), now)
        context = ReportRunContext(market=market, news=news, hours=hours, timezone_name=timezone_name)
        with activate_context(context):
            report = current(summaries, hours, timezone_name)
        print(
            "[report-integrity] "
            f"kind={kind} snapshot={market.captured_at.isoformat(timespec='seconds')} "
            f"allowed={','.join(market.allowed_index_fields)} selected={len(news.selected)} displayed={news.displayed_count}"
        )
        return report

    wrapped._report_integrity_installed = True
    wrapped._report_integrity_original = current
    strict_report_v2.build_markdown_report = wrapped
    app.build_markdown_report = wrapped
    _INSTALLED = True
    print("[report-integrity] atomic market snapshot + shared news pool + proxy/model safety installed")


def install_dispatch_hook() -> None:
    global _DISPATCH_HOOKED
    if _DISPATCH_HOOKED:
        return
    from . import telegram_dispatch

    current = telegram_dispatch._install_generation_pipeline
    if getattr(current, "_report_integrity_hooked", False):
        _DISPATCH_HOOKED = True
        return

    def wrapped_install_generation_pipeline() -> None:
        current()
        install()

    wrapped_install_generation_pipeline._report_integrity_hooked = True
    wrapped_install_generation_pipeline._report_integrity_original = current
    telegram_dispatch._install_generation_pipeline = wrapped_install_generation_pipeline
    _DISPATCH_HOOKED = True
