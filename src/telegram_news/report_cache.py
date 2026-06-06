from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json

LATEST_REPORT_JSON = Path("reports/latest_report.json")
LATEST_REPORT_MD = Path("reports/latest_report.md")


def save_latest_report(*, report: str, kind: str, hours: int, source: str = "scheduled") -> None:
    LATEST_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now().isoformat(timespec="seconds")
    LATEST_REPORT_MD.write_text(report, encoding="utf-8")
    LATEST_REPORT_JSON.write_text(
        json.dumps(
            {
                "ok": True,
                "kind": kind,
                "hours": hours,
                "source": source,
                "generated_at": generated_at,
                "report": report,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def load_latest_report() -> dict:
    if not LATEST_REPORT_JSON.exists():
        return {
            "ok": False,
            "error": "latest_report_not_found",
            "report": "아직 생성된 뉴스 리포트가 없습니다. 정시 분석 또는 /api/refresh 실행이 먼저 필요합니다.",
        }
    try:
        return json.loads(LATEST_REPORT_JSON.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ok": False,
            "error": "latest_report_read_failed",
            "detail": f"{type(exc).__name__}: {exc}",
            "report": "최신 뉴스 리포트를 읽지 못했습니다.",
        }
