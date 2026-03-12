"""memory: Knowledge base + legacy pattern library stub.

knowledge_base — JSON-backed KB with pattern_log and error_log (active)
pattern_lib    — Legacy stub interface (kept for backward compatibility)
"""

from .knowledge_base import KnowledgeBase, Pattern, ErrorRecord
from .pattern_lib import PatternLib, RvvPattern

__all__ = [
    "KnowledgeBase", "Pattern", "ErrorRecord",
    "PatternLib", "RvvPattern",
]
