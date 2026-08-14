from datetime import datetime
from types import SimpleNamespace

from telegram_news import market_dashboard_report as report


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_grounded_market_research_uses_google_search_and_preserves_sources(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    captured = {}
    payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": '{"macro_releases":[{"name":"PPI","released_at_kst":"2026-08-14 21:30 KST","actual":0.2,"consensus":0.3,"previous":0.1,"unit":"%","surprise":"lower","market_relevance":"금리 부담 완화"}],"upcoming_events":[],"earnings_and_guidance":[],"market_catalysts":[]}'
                        }
                    ]
                },
                "groundingMetadata": {
                    "webSearchQueries": ["US PPI August 2026"],
                    "groundingChunks": [
                        {"web": {"title": "BLS", "uri": "https://www.bls.gov/example"}},
                        {"web": {"title": "BLS duplicate", "uri": "https://www.bls.gov/example"}},
                    ],
                },
            }
        ]
    }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return FakeResponse(payload)

    monkeypatch.setattr(report.requests, "post", fake_post)
    result, engine = report._grounded_market_research(datetime(2026, 8, 14, 7, 30))

    assert captured["json"]["tools"] == [{"google_search": {}}]
    assert result["macro_releases"][0]["name"] == "PPI"
    assert result["sources"] == [{"title": "BLS", "uri": "https://www.bls.gov/example"}]
    assert result["search_queries"] == ["US PPI August 2026"]
    assert engine.startswith("google_search_grounding:")


def test_grounded_market_research_failure_is_nonfatal(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    def fail(*args, **kwargs):
        raise TimeoutError("network")

    monkeypatch.setattr(report.requests, "post", fail)
    result, engine = report._grounded_market_research(datetime(2026, 8, 14, 7, 30))

    assert result == {}
    assert engine == "grounding_request_failed:TimeoutError"


def test_local_fallback_displays_macro_actual_consensus_previous_and_schedule():
    payload = {
        "generated_at_iso": "2026-08-14T07:30:00+09:00",
        "market": {
            "global_market_quotes": [],
            "korea_proxies": [],
            "sector_baskets": {},
            "risk_regime": {"regime": "NEUTRAL"},
            "supply_demand_line": "수급 확인불가",
            "market_bias": "중립",
        },
        "grounded_research": {
            "macro_releases": [
                {
                    "name": "PPI",
                    "actual": 0.2,
                    "consensus": 0.3,
                    "previous": 0.1,
                    "unit": "%",
                    "market_relevance": "예상 하회",
                }
            ],
            "upcoming_events": [
                {
                    "name": "소매판매",
                    "scheduled_at_kst": "2026-08-14 21:30 KST",
                    "consensus": 0.4,
                    "unit": "%",
                }
            ],
            "earnings_and_guidance": [],
            "market_catalysts": [],
        },
    }

    text = report._local(payload, clusters=[], rule="test")

    assert "📈 핵심 경제지표" in text
    assert "PPI: 실제 0.2% / 예상 0.3% / 이전 0.1%" in text
    assert "2026-08-14 21:30 KST · 소매판매 · 예상 0.4%" in text
