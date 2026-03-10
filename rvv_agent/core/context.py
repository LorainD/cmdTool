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
