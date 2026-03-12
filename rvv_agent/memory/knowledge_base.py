"""memory.knowledge_base — Self-evolving knowledge base for RVV migration.

Stores two kinds of records:
  - **Pattern**: successful RVV migration patterns (semantic IR, SIMD strategy,
    architecture-specific details) that can be retrieved to guide future
    generations.
  - **ErrorRecord**: recurring build/test errors and their proven fix
    strategies.

Storage: a single JSON file (``knowledge_base.json`` by default).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Pattern:
    """A reusable RVV migration pattern."""
    pattern_id: str = ""
    source: dict = field(default_factory=dict)
    semantic_ir: dict = field(default_factory=dict)
    simd_strategy: dict = field(default_factory=dict)
    architecture: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=lambda: {
        "weight": 0.5,
        "success_count": 0,
        "fail_count": 0,
    })
    notes: str = ""


@dataclass
class ErrorRecord:
    """A recurring error pattern and its fix strategy."""
    error_class: str = ""       # compile_error | link_error | runtime_error | test_mismatch
    pattern: str = ""           # description of the error pattern
    fix_strategy: str = ""      # proven fix approach
    example: str = ""           # concrete example (error text snippet)
    count: int = 1


class KnowledgeBase:
    """JSON-backed knowledge base with basic CRUD and keyword search."""

    def __init__(self, path: Path | str = "knowledge_base.json") -> None:
        self.path = Path(path)
        self.patterns: list[Pattern] = []
        self.errors: list[ErrorRecord] = []

    # ── persistence ──────────────────────────────────────────────────────

    def load(self) -> None:
        """Load from JSON file.  No-op if file does not exist."""
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.patterns = [Pattern(**p) for p in data.get("patterns", [])]
            self.errors = [ErrorRecord(**e) for e in data.get("errors", [])]
        except Exception:
            pass  # corrupted file — start fresh

    def save(self) -> None:
        """Persist to JSON file."""
        data = {
            "patterns": [asdict(p) for p in self.patterns],
            "errors": [asdict(e) for e in self.errors],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── pattern CRUD ─────────────────────────────────────────────────────

    def add_pattern(self, p: Pattern) -> None:
        # Deduplicate by pattern_id
        self.patterns = [x for x in self.patterns if x.pattern_id != p.pattern_id]
        self.patterns.append(p)

    def search_patterns(
        self,
        *,
        symbol: str | None = None,
        algorithm_class: str | None = None,
        tags: list[str] | None = None,
        max_results: int = 10,
    ) -> list[Pattern]:
        """Filter patterns by keyword fields."""
        results: list[Pattern] = []
        for p in self.patterns:
            if symbol and symbol not in p.source.get("symbol", ""):
                continue
            if algorithm_class and algorithm_class != p.semantic_ir.get("algorithm_class", ""):
                continue
            if tags:
                p_tags = set(p.semantic_ir.get("tags", []))
                if not p_tags.intersection(tags):
                    continue
            results.append(p)
            if len(results) >= max_results:
                break
        # Sort by weight descending
        results.sort(key=lambda x: x.metadata.get("weight", 0), reverse=True)
        return results

    def update_weight(self, pattern_id: str, success: bool) -> None:
        """Adjust pattern weight: success → +1, failure → -0.5."""
        for p in self.patterns:
            if p.pattern_id == pattern_id:
                if success:
                    p.metadata["success_count"] = p.metadata.get("success_count", 0) + 1
                    p.metadata["weight"] = p.metadata.get("weight", 0.5) + 1.0
                else:
                    p.metadata["fail_count"] = p.metadata.get("fail_count", 0) + 1
                    p.metadata["weight"] = max(0.0, p.metadata.get("weight", 0.5) - 0.5)
                break

    # ── error CRUD ───────────────────────────────────────────────────────

    def add_error(self, e: ErrorRecord) -> None:
        # Merge with existing record if same class + pattern
        for existing in self.errors:
            if existing.error_class == e.error_class and existing.pattern == e.pattern:
                existing.count += 1
                if e.fix_strategy:
                    existing.fix_strategy = e.fix_strategy
                return
        self.errors.append(e)

    def search_errors(
        self,
        *,
        error_class: str | None = None,
        keyword: str | None = None,
        max_results: int = 10,
    ) -> list[ErrorRecord]:
        results: list[ErrorRecord] = []
        for e in self.errors:
            if error_class and e.error_class != error_class:
                continue
            if keyword and keyword.lower() not in (e.pattern + e.fix_strategy).lower():
                continue
            results.append(e)
            if len(results) >= max_results:
                break
        results.sort(key=lambda x: x.count, reverse=True)
        return results
