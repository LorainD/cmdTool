from __future__ import annotations

import getpass


def prompt_text(prompt: str) -> str:
    return input(prompt)


def prompt_yes_no(prompt: str, *, default: bool | None = None) -> bool:
    if default is True:
        suffix = " [Y/n] "
    elif default is False:
        suffix = " [y/N] "
    else:
        suffix = " [y/n] "

    while True:
        ans = input(prompt + suffix).strip().lower()
        if not ans and default is not None:
            return default
        if ans in {"y", "yes", "1"}:
            return True
        if ans in {"n", "no", "0"}:
            return False
        print("请输入 y 或 n")


def prompt_secret(prompt: str) -> str:
    return getpass.getpass(prompt)
