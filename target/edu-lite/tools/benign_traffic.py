"""良性流量发生器 —— 蓝方误报率分母 & judge 训练标签源②（SPEC §10/§13）。

模拟正常用户会话访问 edu-lite，产出带 label="benign" 的流量记录。
--capture 时记录响应片段（验收 harness 用于零 marker 误命中断言）。
用法: python3 benign_traffic.py --base http://127.0.0.1:8081 --instance a \
        --sessions 5 --out /tmp/benign_flows.jsonl [--capture]
"""
import argparse
import io
import json
import random
import time

import requests

CFG = {
    "a": dict(search="q", avatar="avatar", sig="signature", users=[("alice", "alice123"), ("bob", "bob123")]),
    "b": dict(search="keyword", avatar="picture", sig="motto", users=[("alice", "alice123"), ("bob", "bob123")]),
}
BENIGN_KW = ["考试", "图书馆", "运动", "社团", "放假", "课程", "讲座", "campus", "news", "2026"]
BENIGN_SIG = ["好好学习", "天天向上", "爱跑步", "reader", "静水流深", ""]
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d4944415478da636460f8ff9f0001050102caca24b5"
    "0000000049454e44ae426082"
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    ap.add_argument("--instance", default="a", choices=["a", "b"])
    ap.add_argument("--sessions", type=int, default=5)
    ap.add_argument("--out", default="/tmp/benign_flows.jsonl")
    ap.add_argument("--capture", action="store_true", help="记录响应片段（前 200 字符）")
    a = ap.parse_args()
    cfg = CFG[a.instance]
    flows = []
    for i in range(a.sessions):
        s = requests.Session()
        user, pwd = random.choice(cfg["users"])
        r = s.post(f"{a.base}/login", data={"username": user, "password": pwd}, allow_redirects=False)
        if r.status_code not in (302, 303):
            print(f"session {i}: login failed {r.status_code}")
            continue
        for _ in range(random.randint(3, 7)):
            act = random.random()
            if act < 0.35:
                kw = random.choice(BENIGN_KW)
                r = s.get(f"{a.base}/news/search", params={cfg["search"]: kw})
                flows.append(("search", kw, r))
            elif act < 0.55:
                r = s.get(f"{a.base}/news")
                flows.append(("news", "", r))
            elif act < 0.75:
                r = s.post(f"{a.base}/profile", data={cfg["sig"]: random.choice(BENIGN_SIG)})
                flows.append(("profile", "", r))
            elif act < 0.9:
                r = s.post(f"{a.base}/profile/avatar",
                           files={cfg["avatar"]: (f"av{i}.png", io.BytesIO(PNG), "image/png")})
                flows.append(("avatar", "valid.png", r))
            else:
                r = s.get(f"{a.base}/profile")
                flows.append(("view", "", r))
            time.sleep(random.uniform(0.05, 0.3))
        s.get(f"{a.base}/logout")
    with open(a.out, "a", encoding="utf-8") as f:
        for kind, detail, r in flows:
            rec = {"ts": time.time(), "instance": a.instance, "label": "benign",
                   "kind": kind, "detail": detail[:40], "status": r.status_code}
            if a.capture:
                rec["response_snippet"] = r.text[:200]
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"done: {len(flows)} benign flows -> {a.out}")


if __name__ == "__main__":
    main()
