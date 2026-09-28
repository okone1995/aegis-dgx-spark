"""补丁应用器 —— 模板精确匹配应用（SPEC §11 三层补丁的第一层执行）。

D3：TARGET_BLOCK 精确匹配 + REPLACE_WITH 替换 + diff 存黑板；
LLM 适配（插入点定位/变量名适配，处理模板不精确命中的情形）D4 接入。
"""
from __future__ import annotations

import difflib
import pathlib
import re
from dataclasses import dataclass
from typing import Optional, Tuple


class PatchError(Exception):
    pass


@dataclass
class PatchResult:
    target_file: str
    patch_file: str
    diff: str
    replaced: bool
    modes: List[str] = None  # 每块的应用方式：exact / llm（LLM 模式产物须过 patch_review 三重门禁）


def load_template(tpl_path: pathlib.Path):
    """解析 fix.patch.tpl 的 TARGET_BLOCK[_n] / REPLACE_WITH[_n] 块对（支持多处修改点）。"""
    text = tpl_path.read_text(encoding="utf-8")

    def block(name: str) -> Optional[str]:
        m = re.search(rf"^{name}:\s*\|\n((?:[ \t]+.*\n?)*)", text, re.M)
        if not m:
            return None
        lines = m.group(1).splitlines()
        indented = [ln for ln in lines if ln.strip()]
        if not indented:
            return None
        pad = min(len(ln) - len(ln.lstrip()) for ln in indented)
        return "\n".join(ln[pad:] if len(ln) >= pad else "" for ln in lines).rstrip("\n")

    pairs = []
    for suffix in ["", "_2", "_3", "_4"]:
        t, r = block(f"TARGET_BLOCK{suffix}"), block(f"REPLACE_WITH{suffix}")
        if t and r:
            pairs.append((t, r))
    if not pairs:
        raise PatchError(f"template has no block pairs: {tpl_path}")
    return pairs


def _indent(block: str, pad: int) -> str:
    return "\n".join((" " * pad + ln) if ln.strip() else ln for ln in block.splitlines())


def apply_template(source: pathlib.Path, tpl_path: pathlib.Path,
                   out: Optional[pathlib.Path] = None,
                   llm_fallback=None, llm_first: bool = False) -> PatchResult:
    """对 source 应用模板全部块对；缩进自适应精确匹配。

    llm_fallback(source_text, target_text, replace_text) -> 改写后完整文件。
    默认模板优先、LLM 兜底（某块匹配失败时调用）；llm_first=True 时反转——
    显式选择 LLM 的运行以 LLM 适配先行（模板降为失败兜底），供演示/独白观测。
    """
    pairs = load_template(tpl_path)
    src = source.read_text(encoding="utf-8")
    applied = []

    if llm_first and llm_fallback is not None:
        try:
            new_src = llm_fallback(src, pairs[0][0], pairs[0][1])
        except Exception as e:  # noqa: BLE001 —— LLM 失败落回模板路径
            print(f"[patch] LLM-first 失败，落回模板: {str(e)[:120]}")
        else:
            if new_src and new_src.strip() != src.strip():
                src = new_src
                applied.append((pairs[0][0], pairs[0][1], "llm"))
                original = source.read_text(encoding="utf-8")
                out = out or source
                out.write_text(src, encoding="utf-8", newline="\n")
                diff = "".join(difflib.unified_diff(
                    original.splitlines(True), src.splitlines(True),
                    fromfile=str(source), tofile=str(out)))
                return PatchResult(target_file=str(out), patch_file="", diff=diff,
                                   replaced=True, modes=["llm"])
            print("[patch] LLM-first 未产生有效改写，落回模板")

    for target, replace in pairs:
        done = False
        for pad in range(0, 20, 4):
            candidate = _indent(target, pad)
            if candidate in src:
                src = src.replace(candidate, _indent(replace, pad), 1)
                applied.append((target, replace, "exact"))
                done = True
                break
        if not done:
            if llm_fallback is None:
                raise PatchError(
                    f"TARGET_BLOCK not found (indent-adaptive) in {source}（需 LLM 适配层）")
            patched_src = llm_fallback(src, target, replace)
            if not patched_src or patched_src.strip() == src.strip():
                raise PatchError("LLM 适配层未产生有效改写")
            src = patched_src
            applied.append((target, replace, "llm"))
    original = source.read_text(encoding="utf-8")
    out = out or source
    out.write_text(src, encoding="utf-8", newline="\n")
    diff = "".join(difflib.unified_diff(
        original.splitlines(True), src.splitlines(True),
        fromfile=str(source), tofile=str(out)))
    modes = [m for _, _, m in applied]
    return PatchResult(target_file=str(out), patch_file="", diff=diff,
                       replaced=True, modes=modes)
