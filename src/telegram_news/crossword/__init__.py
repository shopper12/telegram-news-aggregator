"""Daily Korean/English crossword feature for the unified news bot."""

from .integration import install
from .service import CrosswordService

__all__ = ["CrosswordService", "install"]
