"""文本规范化，用于去重哈希。"""

from __future__ import annotations

import re


def normalize(text: str) -> str:
    """小写、压缩空白、去除常见噪声标点，仅用于稳定哈希。

    注意：不要在 raw string 中把换行写为 \\n —— 那会匹配字面字符 'n'。
    空白（含换行/制表符）统一由 [\\s　] 压缩为单空格。
    """
    lowered = text.strip().lower()
    lowered = re.sub(r"[\s　]+", " ", lowered)
    lowered = re.sub("[`\"']+", "", lowered)
    return lowered
