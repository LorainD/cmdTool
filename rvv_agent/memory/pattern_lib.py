"""Pattern library interface stub.

定义存储/检索 RVV 代码模式的接口（暂未实现）。

RvvPattern  : 单条模式记录（数据类型、汇编片段、使用场景等）
PatternLib  : 增删查接口（全部 raise NotImplementedError）

实现方向：
    - 简单版：JSON 文件序列化
    - 增强版：向量检索（embedding），支持语义相似度搜索
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RvvPattern:
    """单条 RVV 代码模式。"""
    symbol: str
    """对应的 FFmpeg 算子名，如 'sbrdsp.neg_odd_64'。"""
    pattern_type: str
    """模式类型，如 'butterfly', 'reduction', 'vsetvli_idiom', 'stride_load' 等。"""
    asm_snippet: str
    """具有代表性的 RVV 汇编片段。"""
    data_type: str
    """操作的数据类型，如 'float32', 'int16', 'uint8' 等。"""
    description: str
    """自然语言描述，说明该模式解决的问题及使用场景。"""
    source_run: str = ""
    """产生该模式的迁移 run 目录，用于溯源。"""
    tags: list[str] = field(default_factory=list)
    """额外标签，用于过滤检索，如 ['vfnmacc', 'butterfly', 'float']。"""


class PatternLib:
    """RVV 代码模式库接口。

    ⚠️  尚未实现 — 所有方法抛出 NotImplementedError。
    """

    def add_pattern(self, pattern: RvvPattern) -> None:
        """向库中添加一条模式记录。"""
        raise NotImplementedError

    def remove_pattern(self, symbol: str, pattern_type: str) -> bool:
        """删除指定 symbol + pattern_type 的记录，返回是否找到并删除。"""
        raise NotImplementedError

    def search_patterns(
        self,
        query: str,
        *,
        pattern_type: str | None = None,
        data_type: str | None = None,
        max_results: int = 10,
    ) -> list[RvvPattern]:
        """按关键词/类型检索模式（未来支持语义向量搜索）。"""
        raise NotImplementedError

    def get_by_symbol(self, symbol: str) -> list[RvvPattern]:
        """获取某个算子的所有模式记录。"""
        raise NotImplementedError

    def load(self, path: str) -> None:
        """从文件（JSON/SQLite）加载模式库。"""
        raise NotImplementedError

    def save(self, path: str) -> None:
        """将模式库保存到文件。"""
        raise NotImplementedError

    def __len__(self) -> int:
        raise NotImplementedError
