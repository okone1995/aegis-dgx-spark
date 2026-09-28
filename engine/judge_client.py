"""T3 判官客户端 —— 任务进程侧的 JEV(Judge)调用薄层(契约 §2.8)。

零第三方依赖(urllib.request);真值隔离:judge() 的输入是纯观测字段
(method/path/body/status/response),绝不含 label/marker_hits 等真值。
判读去重键 (run_id, flow_id, model_version, protocol_id) —— 统计从
本轮持久化记录恢复,不用跨 run 全局行号(契约 §6.1)。
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

# 渲染统一(契约 §2.8):本文件**不得**自带渲染实现,一律委派 dataset/build_judge。
# 该目录以扁平模块名 build_judge 入 sys.path —— 与 dataset/build_judge_v2_dvwa.py
# 复用同源实现的做法一致;`import dataset.build_judge` 会被同名第三方包遮住,
# 故不用包路径。
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "dataset"))
import build_judge as _BJ  # noqa: E402

JUDGE_URL = os.environ.get("AEGIS_JUDGE_URL", "http://127.0.0.1:30002")
_TIMEOUT_DEFAULT = 30.0


class JudgeUnavailable(Exception):
    """判官服务不可达/超时/5xx —— 调用方按 unavailable 处理,不阻塞主流程。"""


def _post(path: str, payload: dict, timeout_s: float) -> dict:
    url = JUDGE_URL.rstrip("/") + path
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise JudgeUnavailable(f"judge service 5xx: {e.code}") from e
        # 4xx 是调用方 bug,原样抛便于定位
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise JudgeUnavailable(f"judge service unreachable: {e}") from e


def _get(path: str, timeout_s: float) -> dict:
    url = JUDGE_URL.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise JudgeUnavailable(f"judge service 5xx: {e.code}") from e
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise JudgeUnavailable(f"judge service unreachable: {e}") from e


def render_user(method: str, path: str, body: str, status: int,
                response: str, with_response: bool = True) -> str:
    """输入渲染 —— 委派 dataset/build_judge.render_user(**唯一实现**)。

    截断(REQ_CAP=700/RESP_CAP=1200)、脱敏(scrub)、拼接顺序与训练卷共享同一份
    代码;以前这里自写一版且缺 scrub,是线上/离线不同源的来源之一。
    对拍断言:tests/test_judge_rendering_parity.py。
    """
    return _BJ.render_user(method, path, body, status, response,
                           with_response=with_response)


def judge(data: dict, timeout_s: float = _TIMEOUT_DEFAULT) -> dict:
    """对一条纯观测 HTTP 交换判读。

    data 只需观测字段(method/path/body/status/response);返回
    p_attack/verdict/raw_verdict/band/latency_ms/model_version/protocol_id。
    悬置带 (0.40,0.60) 内 verdict=abstain(T3 行为契约),raw_verdict 保留
    阈值二分类结果。超时/不可达抛 JudgeUnavailable。
    """
    payload = {k: data.get(k) for k in ("method", "path", "body", "status",
                                        "response")}
    out = _post("/judge", payload, timeout_s)
    out.setdefault("model_version", "unknown")
    # 遗留缺陷(S3 登记,本轮未修):服务未自报协议时这里回填具体版本号,等于把没
    # 自报版本的服务**伪装**成该版本记录;版本是溯源依据,正确缺省是 "unknown"
    # (与 model_version 同法)。未修原因:tests/test_judge_client.py 把该缺省钉成
    # 断言,而该文件含 `password=` 测试占位字面量、写入被凭据闸门拦下,需由持有者
    # 决定后一并改(改法:本行 → out.setdefault("protocol_id", "unknown")).
    # 遗留缺陷(S3 登记,2026-09-27 评审要求修,但未落地):服务未自报协议时这里
    # 回填具体版本号,等于把没自报版本的服务**伪装**成该版本记录;版本是溯源依据,
    # 正确缺省应为 "unknown"(与 model_version 同法)。
    # 未修原因:tests/test_judge_client.py 把该缺省钉成断言,而该文件含一处
    # 凭据形状的占位字面量 ⇒ 对该文件的任何写入被凭据闸门拦下。
    # 处置:已上报持有者,待其决定后一并改(改法:本行 → out.setdefault("protocol_id", "unknown"))。
    # 2026-09-27 已按持有者选择落地:先止血——测试文件里那处凭据形状的输入样本改为
    # 运行时拼接(运行时字串完全等价、断言语义不变),再把本行缺省改为 unknown。
    out.setdefault("protocol_id", "unknown")
    return out


def health(timeout_s: float = 5.0) -> dict:
    return _get("/health", timeout_s)


def metadata(timeout_s: float = 5.0) -> dict:
    return _get("/metadata", timeout_s)


PROTOCOL_ID = _BJ.PROTOCOL_ID
"""客户端期望的线上协议版本(与 dataset/build_judge 同源)。

只用于对拍/展示对照,不复写服务端返回值(服务端才是权威)。
"""


def dedupe_key(run_id: str, flow_id: str, model_version: str,
               protocol_id: str) -> str:
    return f"{run_id}|{flow_id}|{model_version}|{protocol_id}"
