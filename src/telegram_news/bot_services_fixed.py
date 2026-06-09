from __future__ import annotations

try:
    from .bot_services_v7 import handle_command as _handle_command
except Exception:
    from .bot_services_v5 import handle_command as _handle_command


def handle_command(*, user_id: str, message: str, latest_report: str) -> str:
    return _handle_command(user_id=user_id, message=message, latest_report=latest_report)
