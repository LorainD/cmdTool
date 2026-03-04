from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .analyze import analyze_with_llm
from .board import build_board_commands, local_checkasm_path, run_with_sshpass
from .config import AppConfig
from .context import build_context_from_files
from .exec import (
    ExecResult,
    configure_argv,
    make_checkasm_argv,
    run_configure,
    run_make_checkasm,
)
from .generate import generate_with_llm, materialize_package
from .intent import parse_intent
from .interactive import prompt_secret, prompt_text, prompt_yes_no
from .plan import fixed_plan
from .report import write_report
from .retrieve import select_references
from .util import ensure_dir, fmt_argv, now_id, slug, write_text


@dataclass
class SessionState:
    apply_ok: bool | None = None
    build_ok: bool | None = None
    scp_ok: bool | None = None
    run_on_board_ok: bool | None = None
    scp_password: str | None = None


def run_chat(cfg: AppConfig) -> int:
    print("rvv-agent 已唤醒：FFmpeg RVV 迁移专用 agent（交互式）。")
    print("输入类似：迁移 ff_vp8_idct16_add  或直接输入 symbol。输入 exit 退出。\n")

    state = SessionState()

    while True:
        try:
            user_text = prompt_text("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            return 0

        if not user_text:
            continue
        if user_text.lower() in {"exit", "quit", ":q"}:
            return 0

        intent = parse_intent(cfg, user_text)
        symbol = intent.symbol
        if not symbol:
            print("没解析出 symbol，请重新输入（例如 ff_vp8_idct16_add）。")
            continue

        plan = fixed_plan(symbol)
        print("\nPlan：")
        for i, s in enumerate(plan.steps, start=1):
            print(f"{i:02d}. {s}")

        if not prompt_yes_no("\n确认按该 plan 继续？", default=True):
            print("已取消，本轮结束。\n")
            continue

        run_dir = Path("runs") / f"{now_id()}_{slug(symbol)}"
        ensure_dir(run_dir)
        write_text(run_dir / "user_input.txt", user_text + "\n")
        write_text(run_dir / "intent_raw.txt", intent.raw + "\n")

        ffmpeg_root = cfg.ffmpeg.root.expanduser().resolve()
        if not ffmpeg_root.exists():
            print(f"ffmpeg_root 不存在：{ffmpeg_root}")
            continue

        retrieval = select_references(cfg, ffmpeg_root, symbol)
        write_text(run_dir / "retrieval_raw.txt", retrieval.raw_text + "\n")
        selected = retrieval.selected

        def _list(key: str) -> list[str]:
            v = selected.get(key, [])
            return [str(x) for x in v] if isinstance(v, list) else []

        selected_files: list[str] = []
        for k in ("c", "x86", "arm", "riscv", "headers", "makefiles", "checkasm"):
            selected_files.extend(_list(k))
        selected_files = list(dict.fromkeys(selected_files))

        print("\n检索/选择出的参考文件：")
        for p in selected_files:
            print(f"- {p}")

        if not prompt_yes_no("\n确认进入分析/生成阶段？", default=True):
            print("已取消，本轮结束。\n")
            continue

        ctx = build_context_from_files(ffmpeg_root, symbol=symbol, files=selected_files)
        write_text(run_dir / "context.txt", ctx)

        analysis = analyze_with_llm(cfg, retrieval.discovery, context_override=ctx)
        gen = generate_with_llm(cfg, symbol, analysis.analysis)

        if state.apply_ok is None:
            state.apply_ok = prompt_yes_no("\n是否允许本次对话把生成文件写入 FFmpeg workspace？", default=False)

        materialized = materialize_package(run_dir, ffmpeg_root, gen.package, apply=bool(state.apply_ok))

        exec_result = ExecResult()

        # Build steps (configure + make checkasm)
        if state.build_ok is None:
            import os

            jobs = max(1, os.cpu_count() or 1)
            build_dir = ffmpeg_root / cfg.ffmpeg.build_dir
            print("\n交叉编译命令（将会在 build 目录执行）：")
            print(f"- cwd: {build_dir}")
            print("- " + fmt_argv(configure_argv(cfg, ffmpeg_root)))
            print("- " + fmt_argv(make_checkasm_argv(jobs=jobs)))
            state.build_ok = prompt_yes_no("\n是否现在执行 configure + 构建 checkasm？", default=False)

        if state.build_ok:
            import os

            jobs = max(1, os.cpu_count() or 1)
            build_dir = ffmpeg_root / cfg.ffmpeg.build_dir
            ensure_dir(build_dir)
            exec_result.configure = run_configure(cfg, ffmpeg_root, build_dir)
            exec_result.make_checkasm = run_make_checkasm(build_dir, jobs)

        # Board steps
        if cfg.board.enabled:
            cmds = build_board_commands(cfg, ffmpeg_root)
            local_bin = local_checkasm_path(ffmpeg_root, str(cfg.ffmpeg.build_dir))

            if not local_bin.exists():
                print(f"\n本地 checkasm 不存在：{local_bin}")
                print("请先在本机执行构建（或在 migrate/chat 里开启 build）。将跳过 scp/板端运行。")
                state.scp_ok = False
                state.run_on_board_ok = False
            else:
                if state.scp_ok is None:
                    print("\n将把 checkasm scp 到测试板：")
                    print("- " + fmt_argv(cmds.scp_argv))
                    state.scp_ok = prompt_yes_no("是否现在执行 scp？", default=False)

                if state.scp_ok:
                    if state.scp_password is None:
                        print("注意：使用 sshpass 会把密码暴露在进程参数里；长期建议改用 SSH key。")
                        state.scp_password = prompt_secret("请输入测试板 SSH 密码（本次对话仅输入一次）： ")
                    res_scp = run_with_sshpass(cmds.scp_argv, state.scp_password)
                    write_text(run_dir / "scp_stdout.txt", res_scp.stdout)
                    write_text(run_dir / "scp_stderr.txt", res_scp.stderr)

                if state.run_on_board_ok is None:
                    print("\n测试板上运行 checkasm 命令：")
                    print("- " + fmt_argv(cmds.ssh_run_argv))
                    state.run_on_board_ok = prompt_yes_no("是否现在在测试板上运行 checkasm？", default=False)

                if state.run_on_board_ok:
                    if state.scp_password is None:
                        print("注意：使用 sshpass 会把密码暴露在进程参数里；长期建议改用 SSH key。")
                        state.scp_password = prompt_secret("请输入测试板 SSH 密码（本次对话仅输入一次）： ")
                    res_run = run_with_sshpass(cmds.ssh_run_argv, state.scp_password)
                    write_text(run_dir / "board_stdout.txt", res_run.stdout)
                    write_text(run_dir / "board_stderr.txt", res_run.stderr)
        else:
            if state.scp_ok is None:
                print("\n未启用 board 配置（rvv_agent.toml [board]）。将跳过 scp/板端运行。")
                state.scp_ok = False
                state.run_on_board_ok = False

        report_path = write_report(
            run_dir,
            plan=plan,
            discovery=retrieval.discovery,
            analysis=analysis,
            generation_raw=gen.raw_text,
            materialized=materialized,
            exec_result=exec_result,
            interaction={
                "intent_llm_used": intent.llm_used,
                "intent_error": intent.error,
                "retrieval_llm_used": retrieval.llm_used,
                "retrieval_error": retrieval.error,
                "apply_ok": state.apply_ok,
                "build_ok": state.build_ok,
                "scp_ok": state.scp_ok,
                "run_on_board_ok": state.run_on_board_ok,
                "board_enabled": cfg.board.enabled,
            },
        )

        print(f"\n本轮完成：report = {report_path}\n")
