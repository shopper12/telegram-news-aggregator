from __future__ import annotations

from typing import Any

from .unified_pipeline import apply_unified_pipeline


def apply(api_module: Any) -> Any:
    apply_unified_pipeline()
    return api_module
