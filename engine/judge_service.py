"""judge v3 shadow 服务 —— 四层栈第二层的在线旁路（B 案 shadow 形态，用户批准）。

加载参赛判官（Qwen3.5-4B + judge_v3 LoRA 合并，bf16），对蓝方规则未命中/低置信的
流量做**旁路复核**：判定只进判官面板（judge-shadow.jsonl），不进告警链、不碰门禁
——shadow 与主路径零耦合（judge 挂/慢/漂，收敛演示零影响；inline 升级为拍摄日可选项）。

prompt 构造与训练/评估完全一致（dataset/build_judge.make_record + eval_logit.prompt_ids），
attack/benign 首 token logit softmax 判读，悬置带 (0.40,0.60) 外才给硬判定。

运行（Spark，sglang venv 含 torch/transformers）:
  PYTHONPATH=$HOME/aegis/models/_tf517:$HOME/aegis/bench/train \
    $HOME/.local/share/sglang-ssd-stream/venv-0.3.0/bin/python \
    engine/judge_service.py --base <底座> --adapter ~/aegis/models/judge_v3_adapter
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import threading
import time

# T3 契约修复(F1):原为三层 parent(指向仓库父目录),bench/train 从未真正
# 进 sys.path,eval_logit 导入全靠启动命令手工 PYTHONPATH。现改两层 parent。
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "bench" / "train"))

sys.path.insert(0, str(ROOT / "dataset"))
try:
    import eval_logit as EL  # noqa: E402  同一判读(prompt_ids/候选 token)
except ModuleNotFoundError as exc:  # 可见失败：不要以 import 崩掉整个服务
    raise SystemExit(
        "判读模块缺失：需要 bench/train/eval_logit.py（或把它的所在目录加入 PYTHONPATH）。"
        f"原始错误: {exc}") from exc
import build_judge as BJ  # noqa: E402  同一渲染(契约 §2.8:唯一实现)

# 以下常量全部委派 dataset/build_judge —— 服务侧**不得**再自带一份渲染或协议常量。
# Protocol v2 起线上 prompt 与训练卷同源(差异仅 scrub,见 BJ.render_user 注释);
# 对拍见 tests/test_judge_rendering_parity.py(任一侧分叉即红)。
SYSTEM = BJ.SYSTEM
REQ_CAP, RESP_CAP = BJ.REQ_CAP, BJ.RESP_CAP
ABSTAIN_LO, ABSTAIN_HI = BJ.ABSTAIN_LO, BJ.ABSTAIN_HI
PROTOCOL_ID = BJ.PROTOCOL_ID
RENDER_SPEC = BJ.RENDER_SPEC
# 契约 2.8:模型调用串行化(有界队列)——等锁 60s 拿不到回 503,
# 不任由 HTTP 线程无限并发直打 GPU。
_GPU_SEMA = threading.Semaphore(1)


def build_shadow_record(e: dict) -> dict:
    """flow.jsonl 条目 → 与训练/评估同构的判读输入。

    渲染委派 dataset/build_judge.inference_record(唯一实现),此处只做
    观测字段 → 参数的映射,不再自带拼接逻辑(旧实现缺 scrub,与训练卷不同源)。
    response 原样传入(不预先 `or ""`):空/缺失都判为 request_only 形态——
    线上无法区分“响应体为空”与“未采集到响应”,取保守一侧:不编造响应段。
    """
    return BJ.inference_record(e.get("method", ""), e.get("path", ""),
                               e.get("body"), e.get("status", ""),
                               e.get("response"))


def main() -> int:
    ap = argparse.ArgumentParser()
    # 2026-09-27 修（训练侧点名）：原来的缺省被脱敏成了带占位符的宿主路径字面量，
    # 裸跑直接 FATAL。脱敏应改在"写出处"，不该改可执行缺省 —— 这里改成按家目录拼，
    # 两台机器（本机 Windows / Spark Linux）都能落地，也不再含任何宿主路径字面量。
    _home = pathlib.Path.home()
    _default_base = str(_home / "aegis" / "models" / "ms" / "models"
                        / "Qwen--Qwen3.5-4B" / "snapshots" / "master")
    _default_adapter = str(_home / "aegis" / "models" / "judge_v3_adapter")
    ap.add_argument("--base", default=_default_base)
    ap.add_argument("--adapter", default=_default_adapter)
    ap.add_argument("--port", type=int, default=30002)
    a = ap.parse_args()

    from transformers import AutoModelForCausalLM, AutoTokenizer
    from peft import PeftModel
    import torch

    tok = AutoTokenizer.from_pretrained(a.base)
    model = AutoModelForCausalLM.from_pretrained(a.base, dtype=torch.bfloat16,
                                                 device_map={"": 0})
    model = PeftModel.from_pretrained(model, a.adapter).merge_and_unload()
    model.eval()
    a_id = tok(EL.ATTACK_TOK, add_special_tokens=False).input_ids
    b_id = tok(EL.BENIGN_FIRST_TOK, add_special_tokens=False).input_ids
    assert len(a_id) == 1 and len(b_id) == 1, "判读 token 非单 token"
    a_id, b_id = a_id[0], b_id[0]
    print(f"[judge] v3 loaded (bf16 merged) on cuda:0", flush=True)

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    _STARTED_AT = time.time()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            """契约 2.8:/health 与 /metadata。"""
            if self.path == "/health":
                out = json.dumps({
                    "status": "ok", "model_loaded": True,
                    "uptime_s": round(time.time() - _STARTED_AT, 1),
                    "protocol_id": PROTOCOL_ID}).encode()
            elif self.path == "/metadata":
                dev = "unknown"
                try:
                    import torch
                    if torch.cuda.is_available():
                        dev = torch.cuda.get_device_name(0)
                except Exception:  # noqa: BLE001
                    pass
                out = json.dumps({
                    "protocol_id": PROTOCOL_ID,
                    "render_spec": RENDER_SPEC,
                    "abstain_band": [ABSTAIN_LO, ABSTAIN_HI],
                    "band_basis": "online_hard_verdict",
                    # 离线评估口径一并自报:两条带不同源,禁止混算(差异已登记)。
                    "offline_abstain_band": [BJ.EVAL_ABSTAIN_LO, BJ.EVAL_ABSTAIN_HI],
                    "threshold": 0.5, "input_mode": "exchange",
                    "adapter": a.adapter, "base_model": a.base,
                    "device": dev}).encode()
            else:
                self.send_response(404); self.end_headers(); return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def do_POST(self):
            if self.path != "/judge":
                self.send_response(404); self.end_headers(); return
            # 契约 2.8:串行化有界队列——拿不到锁回 503,调用方按
            # unavailable 处理;不无限堆线程打 GPU。
            if not _GPU_SEMA.acquire(timeout=60):
                self.send_response(503); self.end_headers(); return
            try:
                self._judge_locked()
            finally:
                _GPU_SEMA.release()

        def _judge_locked(self):
                n = int(self.headers.get("content-length", 0))
                e = json.loads(self.rfile.read(n) or b"{}")
                rec = build_shadow_record(e)
                ids = EL.prompt_ids(tok, rec)
                x = torch.tensor([ids], device="cuda")
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                s = time.perf_counter()
                with torch.no_grad():
                    logits = model(input_ids=x,
                                   attention_mask=torch.ones_like(x)).logits[:, -1, :]
                    two = torch.stack([logits[:, a_id], logits[:, b_id]], dim=-1).float()
                    pr = torch.softmax(two, dim=-1)[0, 0].item()
                if torch.cuda.is_available():
                    torch.cuda.synchronize()
                ms = round((time.perf_counter() - s) * 1000, 1)
                # 契约 2.8(F7):悬置带内 verdict=abstain,raw_verdict 保留
                # 阈值二分类;统计不得把 abstain 默认为 attack/benign。
                raw = "attack" if pr >= 0.5 else "benign"
                if ABSTAIN_LO < pr < ABSTAIN_HI:
                    verdict, band = "abstain", "abstain"
                else:
                    verdict, band = raw, "confident"
                out = json.dumps({"p_attack": round(pr, 4), "verdict": verdict,
                                  "raw_verdict": raw, "band": band,
                                  "protocol_id": PROTOCOL_ID,
                                  # 渲染级 hash:与训练卷同源的凭证(单次自检即可发现
                                  # 渲染分叉),与 demo_case.input_hash(观测级)互补。
                                  "render_spec": RENDER_SPEC,
                                  "prompt_hash": BJ.prompt_hash(rec),
                                  # T7 真机实测:响应曾缺模型身份 → judge.jsonl
                                  # 的 model_version 恒 unknown(MF2 要求可核验)
                                  "model_version": pathlib.Path(a.adapter).name,
                                  "adapter": a.adapter, "latency_ms": ms,
                                  "n_prompt_tokens": len(ids)}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        def log_message(self, *a2):
            pass

    print(f"[judge] shadow service on http://127.0.0.1:{a.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
