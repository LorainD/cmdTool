"""memory: Pattern library stub — 存储从迁移经验中提炼的 RVV 代码模式。

当前仅定义接口，不含具体实现。
后续可持久化到 JSON/SQLite 并与 agent/evolve.py 协同工作。
"""

from .pattern_lib import PatternLib, RvvPattern

__all__ = ["PatternLib", "RvvPattern"]
