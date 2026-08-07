import ast
from pathlib import Path
import runpy
from types import SimpleNamespace


SCRIPT = Path("scripts/run_api_v7.py")


def test_heavy_runtime_patch_imports_are_not_top_level():
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    top_level_from_imports = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "telegram_news.consistency_rules" not in top_level_from_imports
    assert "telegram_news.market_note_formatter" not in top_level_from_imports


def test_fast_news_path_uses_cached_report_without_slow_fallback():
    namespace = runpy.run_path(str(SCRIPT), run_name="render_fast_start_test")
    install_fast_news_path = namespace["_install_fast_news_path"]
    slow_calls: list[str] = []

    fake_api = SimpleNamespace(
        _news=lambda: slow_calls.append("slow") or "slow fallback",
        load_latest_report=lambda: {"report": "최신 캐시 뉴스"},
    )

    install_fast_news_path(fake_api)

    assert fake_api._news() == "최신 캐시 뉴스"
    assert slow_calls == []


def test_fast_news_path_falls_back_when_cache_is_empty():
    namespace = runpy.run_path(str(SCRIPT), run_name="render_fast_start_test_empty")
    install_fast_news_path = namespace["_install_fast_news_path"]

    fake_api = SimpleNamespace(
        _news=lambda: "slow fallback",
        load_latest_report=lambda: {"report": ""},
    )

    install_fast_news_path(fake_api)

    assert fake_api._news() == "slow fallback"
