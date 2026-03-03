from __future__ import annotations

from pathlib import Path

from .analyze import AnalysisResult
from .exec import ExecResult
from .plan import Plan
from .search import Discovery, group_files
from .util import CmdResult, ensure_dir, fmt_argv, write_json, write_text


def _cmd_section(title: str, res: CmdResult | None) -> str:
    if res is None:
        return f"## {title}\n\n- (skipped)\n"

    out = res.stdout.strip()
    err = res.stderr.strip()

    s = [
        f"## {title}\n",
        "```\n" + "$ " + fmt_argv(res.argv) + f"\n(rc={res.returncode})\n" + "```\n",
    ]
    if out:
        s.append("### stdout\n\n```\n" + out[:20000] + "\n```\n")
    if err:
        s.append("### stderr\n\n```\n" + err[:20000] + "\n```\n")
    return "\n".join(s)


def write_report(
    run_dir: Path,
    *,
    plan: Plan,
    discovery: Discovery,
    analysis: AnalysisResult,
    generation_raw: str,
    materialized: list[Path],
    exec_result: ExecResult,
) -> Path:
    ensure_dir(run_dir)

    groups = group_files(discovery)

    md: list[str] = []
    md.append(f"# rvv-agent run report\n")
    md.append("## Symbol\n\n" + f"- {discovery.symbol}\n")

    md.append("## Plan\n\n" + "\n".join(f"- {s}" for s in plan.steps) + "\n")

    md.append("## Discovery\n")
    for k, v in groups.items():
        md.append(f"### {k}\n")
        md.append("\n".join(f"- {x}" for x in v) if v else "- (none)")
        md.append("")

    md.append("## Matches (first 200)\n")
    for m in discovery.matches[:200]:
        md.append(f"- {m.file}:{m.line}: {m.text}")

    md.append("\n## Analysis JSON\n")
    md.append("```json\n" + __import__("json").dumps(analysis.analysis, ensure_ascii=False, indent=2) + "\n```\n")
    md.append(f"- llm_used: {analysis.llm_used}\n")
    if analysis.error:
        md.append(f"- error: {analysis.error}\n")

    md.append("## Generation (raw)\n")
    md.append("```\n" + generation_raw[:20000] + "\n```\n")

    md.append("## Materialized\n")
    md.append("\n".join(f"- {p}" for p in materialized) if materialized else "- (none)")

    md.append("")
    md.append(_cmd_section("configure", exec_result.configure))
    md.append(_cmd_section("make checkasm", exec_result.make_checkasm))

    report_path = run_dir / "report.md"
    write_text(report_path, "\n".join(md))

    # raw json outputs
    write_json(run_dir / "discovery.json", {"symbol": discovery.symbol, "matches": [m.__dict__ for m in discovery.matches]})
    write_json(run_dir / "analysis.json", analysis.analysis)

    return report_path
