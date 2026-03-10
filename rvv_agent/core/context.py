"""core.context — MigrationContext

Single state container for the RVV migration pipeline.
All pipeline stages read from and write to this shared context object,
following the pattern::

    ctx = stage(ctx)

Fields typed ``Any`` hold internal stage results (Discovery,
GenerationResult, etc.) using ``Any`` to avoid circular imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional



import json


# ---------------------------------------------------------------------------
# DynamicContext — chat mode, per-symbol, never compressed
# ---------------------------------------------------------------------------

@dataclass
class DynamicContext:
    """动态上下文：针对单个 symbol 的完整迁移信息，跨阶段实时维护，绝不压缩。

    三层核心内容：
      1. plan        - 迁移计划步骤（LLM生成/人工 refine 后立即更新）
      2. analysis    - 算子语义分析 JSON（LLM生成，refine 时同步修正）
      3. build_errors - 每次 configure / make 构建错误顺序追加

    所有字段均为完整数据，不做任何裁剪。
    """

    symbol: str

    # ── 1. Plan（不可压缩）────────────────────────────────────────────────
    plan_steps: List[str] = field(default_factory=list)

    # ── 2. Analysis（不可压缩，refine 可修正）────────────────────────────
    analysis: Any = field(default_factory=dict)
    analysis_raw: str = ""

    # ── 3. Build errors（每次构建后追加，不压缩）─────────────────────────
    # 每条: {"phase": "configure"|"make", "attempt": int, "error": str}
    build_errors: List[Any] = field(default_factory=list)

    # ── Refine history（每次人工反馈后追加）──────────────────────────────
    refine_history: List[Any] = field(default_factory=list)

    # ── Code context（完整代码片段，不截断）──────────────────────────────
    code_context: str = ""

    def update_plan(self, steps: List[str], feedback: str = "") -> None:
        """更新 plan 并（可选）记录 refine 历史。"""
        self.plan_steps = list(steps)
        if feedback:
            self.refine_history.append({
                "stage": "plan",
                "feedback": feedback,
                "result_steps": list(steps),
            })

    def update_analysis(self, analysis: Any, raw: str, feedback: str = "") -> None:
        """更新 analysis 并（可选）记录 refine 历史。"""
        self.analysis = dict(analysis) if isinstance(analysis, dict) else analysis
        self.analysis_raw = raw
        if feedback:
            self.refine_history.append({
                "stage": "analysis",
                "feedback": feedback,
            })

    def append_build_error(self, phase: str, attempt: int, error_text: str) -> None:
        """追加一次构建错误到上下文（永不删除旧记录）。"""
        self.build_errors.append({
            "phase": phase,
            "attempt": attempt,
            "error": error_text,
        })

    def build_errors_for_llm(self) -> str:
        """将所有历史构建错误格式化为 LLM 可用文本（完整，不压缩）。"""
        if not self.build_errors:
            return ""
        parts = ["# 历次构建错误记录（按时间顺序，供 LLM 参考）:"]
        for e in self.build_errors:
            parts.append(f"\n## [{e['phase']}] 第 {e['attempt']} 次尝试的错误:")
            parts.append(e["error"])
        return "\n".join(parts)

    def analysis_for_llm(self) -> str:
        """将当前分析 JSON 格式化为 LLM 可用文本。"""
        if not self.analysis:
            return ""
        return json.dumps(self.analysis, ensure_ascii=False, indent=2)

    def to_summary(self) -> str:
        """生成一份人可读的上下文摘要（用于调试/报告）。"""
        lines = [f"DynamicContext({self.symbol}):"]
        lines.append(f"  plan_steps: {len(self.plan_steps)} 步")
        lines.append(f"  analysis: {'有' if self.analysis else '无'}")
        lines.append(f"  build_errors: {len(self.build_errors)} 条")
        lines.append(f"  refine_history: {len(self.refine_history)} 条")
        lines.append(f"  code_context: {len(self.code_context)} chars")
        return "\n".join(lines)

@dataclass
class MigrationContext:
    """State container for one RVV migration run.

    Initialised at the start of :func:`pipeline.run_migrate` and threaded
    through each pipeline stage.  Every stage reads its needed inputs from
    *ctx* and writes its outputs back into the same object before returning
    it.

    Future extensions
    -----------------
    * **Self-improving loop** -- ``iteration``, ``score``, ``best_score``
      support future optimisation loops that re-run the pipeline and keep
      the best-scoring result.
    * **Pattern library** -- ``patterns_used`` will record which RVV
      optimisation patterns were applied, feeding :mod:`memory.pattern_lib`.
    """

    # Required
    operator: str
    """Canonical name of the operator / symbol being migrated."""

    repo_root: Path
    """Root of the FFmpeg source tree."""

    # App configuration (injected at pipeline start, not serialised)
    cfg: Optional[Any] = field(default=None, repr=False)
    """AppConfig instance; held here so stage wrappers need no extra arg."""

    # Run workspace
    run_dir: Optional[Path] = None
    """Directory for this run's artefacts, e.g. ``runs/20260309_123456_op/``."""

    iteration: int = 0
    """Current iteration index (0 = first run; >0 = self-improving loop)."""

    # Pipeline execution flags (set at pipeline start)
    do_exec: bool = True
    """Whether to actually run configure + make checkasm."""

    apply: bool = True
    """Whether to write generated files into the FFmpeg workspace."""

    jobs: int = 4
    """Parallel jobs for ``make``."""

    # Search stage
    source_file: Optional[str] = None
    """Primary source file for the operator (relative path string)."""

    reference_files: List[str] = field(default_factory=list)
    """Reference implementation files selected for LLM context."""

    # Internal pipeline state (Any to avoid circular imports)
    discovery: Optional[Any] = field(default=None, repr=False)
    """Full :class:`agent.search.Discovery` result from the search stage."""

    analysis_result: Optional[Any] = field(default=None, repr=False)
    """Full :class:`agent.generate.AnalysisResult` from the analysis stage."""

    current_gen: Optional[Any] = field(default=None, repr=False)
    """Latest :class:`agent.generate.GenerationResult`; updated by fix loop."""

    inject_result: Optional[Any] = field(default=None, repr=False)
    """Latest :class:`agent.inject.InjectResult` from the injection stage."""

    exec_result: Optional[Any] = field(default=None, repr=False)
    """Latest :class:`tool.exec.ExecResult` from the build stage."""

    # Generated artefacts
    generated_files: List[Path] = field(default_factory=list)
    """Paths of files written to disk by the generate/inject stages."""

    # Build / debug info
    build_log: Optional[str] = None
    """Combined stdout+stderr of the most-recent configure or make run."""

    checkasm_output: Optional[str] = None
    """Combined stdout+stderr of the ``make checkasm`` run."""

    # Evaluation (future self-improving loop)
    score: Optional[float] = None
    """Quality score of the current generated code (higher = better)."""

    best_score: Optional[float] = None
    """Best score seen across all iterations of the self-improving loop."""

    # Pattern library hints (future feature)
    patterns_used: List[str] = field(default_factory=list)
    """Names of RVV optimisation patterns applied during generation."""
