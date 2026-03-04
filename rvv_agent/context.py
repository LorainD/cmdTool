from __future__ import annotations

import re
from pathlib import Path


def build_context_from_files(
    ffmpeg_root: Path,
    *,
    symbol: str,
    files: list[str],
    max_total_chars: int = 20000,
    window: int = 3,
) -> str:
    token_re = re.compile(r"\b" + re.escape(symbol) + r"\b")

    chunks: list[str] = []
    total = 0

    for rel in files:
        p = ffmpeg_root / rel
        if not p.exists() or not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        hits: list[int] = [i for i, line in enumerate(lines) if token_re.search(line)]
        if not hits:
            # Still include a small header snippet
            snippet = "\n".join(lines[: min(40, len(lines))])
            block = f"--- {rel} (head) ---\n{snippet}\n"
            chunks.append(block)
            total += len(block)
        else:
            for i in hits[:6]:
                start = max(0, i - window)
                end = min(len(lines), i + window + 1)
                snippet = "\n".join(f"{j+1:6d}: {lines[j]}" for j in range(start, end))
                block = f"--- {rel} (around {symbol} @ line {i+1}) ---\n{snippet}\n"
                chunks.append(block)
                total += len(block)
                if total >= max_total_chars:
                    break

        if total >= max_total_chars:
            break

    out = "\n".join(chunks)
    return out[:max_total_chars]
