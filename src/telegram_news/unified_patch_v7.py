from __future__ import annotations

from typing import Any

from .evidence_summarizer import install as install_evidence_summarizer
from .unified_pipeline import apply_unified_pipeline


def apply(api_module: Any) -> Any:
    apply_unified_pipeline()
    install_evidence_summarizer()
    return api_module
