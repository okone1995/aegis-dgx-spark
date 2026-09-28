"""Runner 适配器 — skill 执行层抽象（SPEC §6 Runner 契约，第 8 项决策）。

StepRunner（Step Code headless）为主，PyRunner 为零依赖回退；
engine 只面向 Runner 接口编程，harness 层可整体切换。
"""
from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List

SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"
HARNESS_ROOT = Path(__file__).resolve().parent.parent / "harness"


class RunnerError(Exception):
    pass


class Runner(ABC):
    """skill 执行器接口：输入黑板上下文，输出结构化产物（落黑板）。"""

    name: str = "base"

    @abstractmethod
    def run_skill(self, skill: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """执行 skills/<skill>/，ctx 至少含 workspace 路径与 provider 配置。"""
        raise NotImplementedError


class PyRunner(Runner):
    """零依赖回退：直接调用 skill 目录内 handler.py 的 run(ctx)（D3 闭环先用它打通）。"""

    name = "pyrunner"

    def run_skill(self, skill: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        skill_dir = SKILLS_ROOT / skill
        if not skill_dir.is_dir():
            raise RunnerError(f"skill not found: {skill_dir}")
        handler = skill_dir / "handler.py"
        if not handler.is_file():
            raise RunnerError(
                f"PyRunner: {skill} 无 handler.py（StepRunner 专属技能或未实现）"
            )
        ns: Dict[str, Any] = {}
        exec(compile(handler.read_text(encoding="utf-8"), str(handler), "exec"), ns)  # noqa: S102
        if "run" not in ns:
            raise RunnerError(f"{skill}/handler.py 未定义 run(ctx)")
        return ns["run"](ctx)


class StepRunner(Runner):
    """Step Code headless 执行（第 8 项决策，D2 spike 四项验证通过后于 D4 实装）。

    ctx["prompt"]：任务指令（SKILL.md 经 --append-system-prompt 注入）。
    provider 经 local-qwen 扩展 + compat shim（harness/stepcode/）。
    """

    name = "steprunner"

    def __init__(self, binary: str = "step",
                 model: str = "Qwen3.8-Flash-Next-NVFP4-SSD-Stream",
                 shim_url: str = "http://127.0.0.1:30005/v1",
                 timeout: int = 300):
        self.binary = binary
        self.model = model
        self.shim_url = shim_url
        self.timeout = timeout

    def run_skill(self, skill: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
        exe = shutil.which(self.binary)
        if exe is None:  # 非交互 shell 不加载 bashrc，回退默认安装路径
            fallback = Path.home() / ".stepcode" / "bin" / self.binary
            if fallback.is_file():
                exe = str(fallback)
        if exe is None:
            raise RunnerError(
                f"step binary not found: {self.binary}（PATH 或 ~/.stepcode/bin）")
        skill_md = SKILLS_ROOT / skill / "SKILL.md"
        if not skill_md.is_file():
            raise RunnerError(f"skill not found: {skill_md}")
        prompt = ctx.get("prompt") or f"按 {skill}/SKILL.md 执行任务：{ctx}"
        cmd = [exe, "-e", str(HARNESS_ROOT / "stepcode/local-qwen.mjs"),
               "--provider", "local-qwen", "--model", self.model,
               "--thinking", "off", "--mode", "text",
               "--no-tools", "--no-session",
               "--append-system-prompt", str(skill_md),
               "-p", prompt]
        env = dict(os.environ, AEGIS_SGLANG_URL=self.shim_url)
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=self.timeout, env=env)
        if proc.returncode != 0:
            raise RunnerError(f"step exited {proc.returncode}: {(proc.stderr or '')[-200:]}")
        return {"text": (proc.stdout or "").strip()}


def get_runner(prefer: str = "py") -> Runner:
    """选择执行器：默认 PyRunner；prefer='step' 且 step 可用时用 StepRunner。"""
    if prefer == "step":
        return StepRunner()
    return PyRunner()
