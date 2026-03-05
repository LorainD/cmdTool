from __future__ import annotations

import json
from dataclasses import dataclass

from .config import AppConfig
from .llm import LlmError, LlmMessage, chat_completion
from .prompts import analysis_prompt, system_prompt
from .search import Discovery, build_llm_context, group_files


@dataclass
class AnalysisResult:
    analysis: dict
    raw_text: str
    llm_used: bool
    error: str | None = None


def _fallback_analysis(discovery: Discovery) -> dict:
    g = group_files(discovery)
    return {
        "symbol": discovery.symbol,
        "datatype": "unknown",
        "vectorizable": True,
        "pattern": [],
        "has_stride": False,
        "has_saturation": False,
        "reduction": False,
        "tail_required": False,
        "math_expression": "unknown",
        "c_candidates": [f"{m.file}:{m.line}" for m in discovery.matches if m.file.endswith(".c")][:20],
        "x86_refs": g["x86_refs"],
        "arm_refs": g["arm_refs"],
        "notes": "LLM 未运行或解析失败，使用 fallback。",
    }


def analyze_with_llm(
    cfg: AppConfig,
    discovery: Discovery,
    *,
    context_override: str | None = None,
) -> AnalysisResult:
    ctx = context_override if context_override is not None else build_llm_context(discovery)
    messages = [
        LlmMessage(role="system", content=system_prompt()),
        LlmMessage(role="user", content=analysis_prompt(discovery.symbol, ctx)),
    ]

    try:
        raw = chat_completion(cfg.llm, messages, max_tokens=1400, stage="analyze")
        data = json.loads(raw)
        return AnalysisResult(analysis=data, raw_text=raw, llm_used=True)
    except LlmError as e:
        fb = _fallback_analysis(discovery)
        return AnalysisResult(analysis=fb, raw_text=str(e), llm_used=False, error=str(e))
    except Exception as e:
        fb = _fallback_analysis(discovery)
        return AnalysisResult(analysis=fb, raw_text=repr(e), llm_used=False, error=repr(e))
