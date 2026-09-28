"""LLM 客户端 —— provider 双通道（SPEC §11：本地主权 / 云增强）。

qwen  → SGLang 30000（127.0.0.1，补丁等主权链路）
step  → api.stepfun.com（报告/复核/补丁对比实验列）
统一 thinking-off / 短输出预算（D1 实验 B 结论）。

内心独白：SSE 流式读取，思考/回复增量实时写入 <AEGIS_MONOLOG 同目录>/llm-live.json
（Console 对话流轮询它实现"边想边显示"）；调用结束写终态记录到 llm-monolog.jsonl
并清除 live 文件。fail-silent：日志失败绝不影响主链路。
"""
from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.request
from typing import Optional

PROVIDERS = {
    "qwen": {
        "url": "http://127.0.0.1:30000/v1/chat/completions",
        "model": "Qwen3.8-Flash-Next-NVFP4-SSD-Stream",
        "key_env": "AEGIS_KEY",
        "default_key": None,  # 密钥只走 env。注意：旧本地服务 key 仍有效、轮换待维护窗口（真实状态以 HANDOFF §7 为准；该 key 已入 git 历史，换 key 才是真修，D9 公开前硬债）
        "no_think": True,
    },
    "step": {
        "url": "https://api.stepfun.com/step_plan/v1/chat/completions",
        "model": "step-3.5-flash",
        "key_env": "STEP_API_KEY",
        "default_key": None,
        "no_think": False,   # step-3.5-flash 自带思考链，用足 max_tokens 预算即可
    },
}


class LLMError(Exception):
    pass


def _live_path() -> Optional[str]:
    mono = os.environ.get("AEGIS_MONOLOG")
    return str((pathlib.Path(mono).parent / "llm-live.json")) if mono else None


def _write_live(provider: str, purpose: str, role: str, phase: str,
                reasoning: str, content: str) -> None:
    lp = _live_path()
    if not lp:
        return
    try:
        import pathlib
        pathlib.Path(lp).write_text(json.dumps({
            "ts": time.time(), "provider": provider, "purpose": purpose,
            "role": role, "phase": phase,
            "reasoning": reasoning[-1500:], "content": content[:600],
        }, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _clear_live() -> None:
    lp = _live_path()
    if lp:
        try:
            os.remove(lp)
        except OSError:
            pass


def _monolog(provider: str, purpose: str, role: str, prompt: str, reasoning: str,
             content: str, wall: float, status: str = "ok", error: str = "") -> None:
    """终态独白落盘（fail-silent：日志失败绝不影响主链路）。"""
    path = os.environ.get("AEGIS_MONOLOG")
    if not path:
        return
    try:
        rec = {
            "ts": time.time(), "provider": provider, "purpose": purpose,
            "role": role, "status": status,
            "prompt_head": prompt[:280], "reasoning": (reasoning or "")[:4000],
            "content_head": (content or "")[:600], "wall_s": round(wall, 1),
            "error": error[:200],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def chat(prompt: str, provider: str = "qwen", max_tokens: int = 800,
         timeout: int = 180, system: Optional[str] = None,
         purpose: str = "chat", role: str = "LLM") -> str:
    """SSE 流式调用：思考/回复增量实时写 llm-live.json（对话流边想边显示）。"""
    cfg = PROVIDERS[provider]
    key = os.environ.get(cfg["key_env"]) or cfg["default_key"]
    if not key:
        raise LLMError(f"{provider}: missing API key ({cfg['key_env']})")
    body: dict = {
        "model": cfg["model"],
        "messages": ([{"role": "system", "content": system}] if system else [])
        + [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
    }
    if cfg["no_think"]:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(
        cfg["url"], data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    t0 = time.time()
    reasoning: list = []
    content: list = []
    phase = "thinking"
    _write_live(provider, purpose, role, phase, "", "")
    last_flush = t0
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            for raw in r:
                line = raw.strip()
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    break
                try:
                    j = json.loads(data)
                except json.JSONDecodeError:
                    continue
                delta = (j.get("choices") or [{}])[0].get("delta") or {}
                rd = delta.get("reasoning_content") or delta.get("reasoning") or ""
                cd = delta.get("content") or ""
                if rd:
                    phase = "thinking"
                    reasoning.append(rd)
                if cd:
                    phase = "writing"
                    content.append(cd)
                now = time.time()
                if now - last_flush >= 0.8:
                    _write_live(provider, purpose, role, phase,
                                "".join(reasoning), "".join(content))
                    last_flush = now
    except Exception as e:  # noqa: BLE001 —— 失败也留独白（诚实账本），再抛给上层
        _monolog(provider, purpose, role, prompt, "".join(reasoning),
                 "".join(content), time.time() - t0, status="error", error=str(e))
        _clear_live()
        raise LLMError(f"{provider}: {str(e)[:160]}") from e
    reasoning_s = "".join(reasoning).strip()
    content_s = "".join(content).strip()
    wall = time.time() - t0
    _monolog(provider, purpose, role, prompt, reasoning_s, content_s, wall)
    _clear_live()
    if not content_s:
        raise LLMError(f"{provider}: empty content (stream)")
    return content_s


def patch_adapt(source_text: str, target_hint: str, strategy: str,
                provider: str = "qwen", timeout: int = 300) -> str:
    """补丁适配层：LLM 按策略在源码中定位并改写漏洞段，返回改写后【完整文件】。"""
    system = ("你是资深 PHP 安全补丁工程师。输出必须是改写后的完整文件内容，"
              "不带任何解释、markdown 代码围栏或前后缀文本。")
    prompt = f"""请修复以下 PHP 文件中的漏洞。

修复策略（参考实现，需适配实际代码的变量名与插入点）：
{strategy}

漏洞段的参考形态（可能与实际代码有出入，以实际代码为准定位）：
{target_hint}

要求：
1. 最小改动，只改漏洞相关代码段
2. 不得引入 PDO（运行时无 pdo_sqlite 扩展）
3. 保持原有缩进与代码风格
4. 直接输出改写后的完整文件

待修复文件（完整内容，原样返回改写后的完整文件，不要额外添加 <?php 标签）：
{source_text}"""
    out = chat(prompt, provider=provider, max_tokens=8000, timeout=timeout,
               system=system, purpose="patch_adapt", role="修补智能体")
    if "```" in out:  # 剥可能的代码围栏
        lines = [ln for ln in out.splitlines() if not ln.strip().startswith("```")]
        out = "\n".join(lines)
    # 防御：剥掉模型多余添加的重复 <?php 头（源文件自带）
    if out.count("<?php") > 1:
        out = out.replace("<?php", "", 1).lstrip()
        out = "<?php" + out if not out.startswith("<?php") else out
    return out
