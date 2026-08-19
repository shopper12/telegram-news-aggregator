from __future__ import annotations

import os


GLOBAL_QUOTE_TICKERS: dict[str, str] = {
    "DOW": "^DJI",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "RUSSELL2000": "^RUT",
    "SOX": "^SOX",
    "DXY": "DX-Y.NYB",
    "VIX": "^VIX",
    "US10Y": "^TNX",
    "US30Y": "^TYX",
    "WTI": "CL=F",
    "BRENT": "BZ=F",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
}

KOREA_PROXY_TICKERS: dict[str, str] = {
    "EWY": "EWY",
    "KORU": "KORU",
    "SOXX": "SOXX",
}

THEME_BASKETS: dict[str, dict[str, str]] = {
    "반도체·메모리": {
        "NVIDIA": "NVDA",
        "Micron": "MU",
        "AMD": "AMD",
        "Intel": "INTC",
    },
    "메모리·스토리지": {
        "Micron": "MU",
        "SanDisk": "SNDK",
        "Western Digital": "WDC",
        "Seagate": "STX",
    },
    "AI 인프라": {
        "CoreWeave": "CRWV",
        "Nebius": "NBIS",
        "SuperMicro": "SMCI",
    },
    "광통신·네트워크": {
        "Fabrinet": "FN",
        "Lumentum": "LITE",
        "Coherent": "COHR",
        "Ciena": "CIEN",
    },
    "소프트웨어": {
        "Salesforce": "CRM",
        "ServiceNow": "NOW",
        "Microsoft": "MSFT",
    },
    "헬스케어·방어주": {
        "Eli Lilly": "LLY",
        "Johnson & Johnson": "JNJ",
        "Procter & Gamble": "PG",
    },
    "빅테크": {
        "Apple": "AAPL",
        "Meta": "META",
        "Microsoft": "MSFT",
    },
    "전력·데이터센터": {
        "Vertiv": "VRT",
        "Eaton": "ETN",
        "Bloom Energy": "BE",
    },
}


def atomic_extra_assets() -> set[str]:
    tickers = set(GLOBAL_QUOTE_TICKERS.values()) | set(KOREA_PROXY_TICKERS.values())
    for members in THEME_BASKETS.values():
        tickers.update(members.values())
    return tickers


def install_atomic_assets() -> None:
    """Extend the one-shot report snapshot only for the US-close morning brief.

    Other hourly/regular reports keep their smaller asset universe so this richer
    briefing does not multiply Yahoo requests across every scheduled run.
    """
    kind = str(os.getenv("BRIEFING_KIND", "regular") or "regular").strip().lower()
    if kind != "us_close":
        return
    from . import report_integrity

    before = len(report_integrity.REPORT_SNAPSHOT_ASSETS)
    report_integrity.REPORT_SNAPSHOT_ASSETS.update(atomic_extra_assets())
    after = len(report_integrity.REPORT_SNAPSHOT_ASSETS)
    print(f"[morning-brief] atomic asset universe extended {before}->{after}")
