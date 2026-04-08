from __future__ import annotations


def split_budget(total_ms: int, emergency: bool) -> tuple[int, int, int]:
    total = max(0, total_ms)
    margin = int(total * 0.10)
    if emergency:
        stage1 = int(total * 0.80)
        stage2 = int(total * 0.10)
    else:
        stage1 = int(total * 0.55)
        stage2 = int(total * 0.35)
    return stage1, stage2, margin
