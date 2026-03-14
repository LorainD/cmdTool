"""core.context — MigrationContext (DEPRECATED).

原为 pipeline 模式的状态容器，现已被 core.task.TaskContext 完全替代。
保留此文件仅用于 core/__init__.py 的 re-export 兼容。

Note: The interactive chat mode uses ``core.task.TaskContext`` instead.
``DynamicContext`` has been removed — its responsibilities are now covered
by TaskContext + per-stage artifacts persisted under ``state/``.
"""
from __future__ import annotations

# from dataclasses import dataclass, field
# from pathlib import Path
# from typing import Any, List, Optional
#
#
# @dataclass
# class MigrationContext:
#     """State container for one RVV migration run (pipeline mode).
#
#     DEPRECATED: 已被 core.task.TaskContext 替代。
#     Pipeline 和 Chat 模式现在都使用 TaskContext + StateMachine。
#
#     Initialised at the start of :func:`pipeline.run_migrate` and threaded
#     through each pipeline stage.
#     """
#
#     # Required
#     operator: str
#     """Canonical name of the operator / symbol being migrated."""
#
#     repo_root: Path
#     """Root of the FFmpeg source tree."""
#
#     cfg: Optional[Any] = field(default=None, repr=False)
#     run_dir: Optional[Path] = None
#     iteration: int = 0
#     do_exec: bool = True
#     apply: bool = True
#     jobs: int = 4
#
#     # Search stage
#     source_file: Optional[str] = None
#     reference_files: List[str] = field(default_factory=list)
#
#     # Internal pipeline state
#     discovery: Optional[Any] = field(default=None, repr=False)
#     analysis_result: Optional[Any] = field(default=None, repr=False)
#     current_gen: Optional[Any] = field(default=None, repr=False)
#     inject_result: Optional[Any] = field(default=None, repr=False)
#     exec_result: Optional[Any] = field(default=None, repr=False)
#
#     # Generated artefacts
#     generated_files: List[Path] = field(default_factory=list)
#
#     # Build / debug info
#     build_log: Optional[str] = None
#     checkasm_output: Optional[str] = None
#
#     # Evaluation (future)
#     score: Optional[float] = None
#     best_score: Optional[float] = None
#     patterns_used: List[str] = field(default_factory=list)
