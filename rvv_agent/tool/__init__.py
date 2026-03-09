"""tool: 执行层 —— 交叉编译、板端操作、终端交互。"""

from .exec import ExecResult, configure_argv, make_checkasm_argv, run_configure, run_make_checkasm
from .board import BoardCommands, build_board_commands, local_checkasm_path, run_with_sshpass
from .interactive import prompt_secret, prompt_text, prompt_yes_no

__all__ = [
    "ExecResult", "configure_argv", "make_checkasm_argv", "run_configure", "run_make_checkasm",
    "BoardCommands", "build_board_commands", "local_checkasm_path", "run_with_sshpass",
    "prompt_secret", "prompt_text", "prompt_yes_no",
]
