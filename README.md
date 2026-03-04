# rvv-agent（cmdTool）

一个面向 FFmpeg 的 RVV 汇编 SIMD 迁移“agent式”命令行工具（MVP）。

当前阶段目标：先把 [idea.md](idea.md) 里描述的自动化流程跑通骨架：
- 固定 plan
- workspace 检索（定位 C / x86 / ARM 等参考）
- 在“分析/生成”阶段调用 LLM（OpenAI-compatible）
- 输出可落盘的补丁包（文件 + diff 建议）
- 可选执行交叉 configure + 构建 checkasm

> 说明：本版本不实现自进化/模式库；生成内容可能是占位骨架（当 LLM 未配置或解析失败时）。

## 目录

- `bin/rvv-agent`：CLI 入口
- `rvv_agent/`：核心代码
- `rvv_agent.toml`：配置（FFmpeg root / toolchain / LLM）
- `runs/`：每次运行的轨迹与产物（report、package、生成文件）
- `workplace/FFmpeg`：FFmpeg 工作区（你的 vendor / clone）

## 使用

在本仓库根目录执行：

- 查看固定迁移流程（plan）

```bash
./bin/rvv-agent plan ff_vp8_idct16_add
```

- 跑一次迁移流水线（默认不改动 FFmpeg，只在 `runs/` 落盘）

```bash
./bin/rvv-agent migrate ff_vp8_idct16_add
```

- 允许把生成的 `files` 写入 FFmpeg workspace（谨慎使用）

```bash
./bin/rvv-agent migrate ff_vp8_idct16_add --apply
```

- 额外执行交叉编译：configure + `make tests/checkasm/checkasm`

```bash
./bin/rvv-agent migrate ff_vp8_idct16_add --exec
```

- 交互式对话入口（推荐）：每一步都会提示确认（plan / 写库 / 编译 / scp / 板端运行）

```bash
./bin/rvv-agent chat
```

- （可选）开启 scp/板端运行：编辑 `rvv_agent.toml` 的 `[board]`，填好 `user/host/remote_dir`，然后把 `enabled=true`。

产物：
- `runs/<timestamp>_<symbol>/report.md`
- `runs/<timestamp>_<symbol>/analysis.json`
- `runs/<timestamp>_<symbol>/discovery.json`
- `runs/<timestamp>_<symbol>/artifacts/package.json`
- `runs/<timestamp>_<symbol>/artifacts/files/...`

## LLM 配置（必须）

本工具走 OpenAI-compatible 的 `POST /v1/chat/completions`。

1) 设置 API Key 环境变量（默认变量名是 `LLM_API_KEY`）：

```bash
export LLM_API_KEY='...'
```

2) 可在 `rvv_agent.toml` 里调整：
- `llm.base_url`（你的网关/代理）
- `llm.model`
- `llm.temperature`

如果没有配置 key，工具会继续跑，但会用 fallback 产出占位骨架并在 report 中记录错误原因。

## 下一步（建议）

- 把 `analysis.json` 的 schema 固化（并做 `analysis_skills/` 校验脚本）
- 给 `generate` 阶段加上“最小可编译”约束：从 init/Makefile 自动提取宏名与函数注册点
- 把 checkasm 运行方式补齐（qemu / ssh 到板子）并把失败时的 diff 输出写进 report
