"""payload policy — 红方每个 payload 发射前的机器校验（SPEC §9）。

原则：宁可误杀不可漏放；被拒 payload 连同理由写入审计日志。
两档 RoE（用户拍板 2026-09-25）：
  strict —— 缺省，永远赢：内容禁忌，与历史行为逐字节一致。
  jail   —— 能力域：爆炸半径由隔离笼的机器证明封顶，不再由词表封顶。
    三硬线任何档位不豁免：①对宿主主机的绝对路径写 ②对第三方的外联
    （改写为 canary 假外联留证）③资源耗尽型 DoS。
    笼内落盘 webshell / rm / DROP / 无 WHERE DELETE 按能力域放行。
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Pattern

# 破坏性写入：禁
DESTRUCTIVE: List[Pattern] = [
    re.compile(r"rm\s+(-[a-z]*r[a-z]*f|-[a-z]*f[a-z]*r)", re.I),
    re.compile(r"\bDROP\s+(TABLE|DATABASE)\b", re.I),
    re.compile(r"\bTRUNCATE\s+TABLE\b", re.I),
    re.compile(r"\bSHUTDOWN\b", re.I),
    re.compile(r"\bmkfs\b", re.I),
    re.compile(r":\(\)\s*\{:\|:&\};:"),                     # fork bomb
    re.compile(r"\bDELETE\s+FROM\s+\w+\s*(;|--|#|$)", re.I | re.M),  # 无 WHERE 整表删（不限分号结尾）
]

# 持久化/webshell：禁（检测样本可以包含回显型 payload，落盘型一律拒绝）
PERSISTENCE: List[Pattern] = [
    re.compile(r"eval\s*\(\s*\$_(POST|GET|REQUEST)", re.I),
    re.compile(r"assert\s*\(\s*\$_(POST|GET|REQUEST)", re.I),
    re.compile(r"\b(system|passthru|shell_exec|exec)\s*\(\s*\$_", re.I),
    re.compile(r"file_put_contents\s*\([^)]*\$_(POST|GET|REQUEST)", re.I),
    re.compile(r"<\?php.*(fwrite|file_put_contents).*\?>", re.I | re.S),
]

# 拒绝服务：禁
DOS: List[Pattern] = [
    re.compile(r"BENCHMARK\s*\(\s*\d{7,}", re.I),
    re.compile(r"\bSLEEP\s*\(\s*\d{3,}", re.I),
    re.compile(r"(.)\1{5000,}"),                             # 超长重复
]

CATEGORY_PATTERNS: Dict[str, List[Pattern]] = {
    "destructive_write": DESTRUCTIVE,
    "persistence": PERSISTENCE,
    "dos": DOS,
}

# 出网：payload 中只允许指向本地采集器
_URL = re.compile(r"https?://[^\s'\"<>]+", re.I)
_LOCAL_EGRESS = re.compile(r"^https?://(127\.0\.0\.1|localhost|::1)(:\d+)?(/.*)?$", re.I)


@dataclass
class Violation:
    category: str
    pattern: str
    snippet: str


class PolicyViolation(Exception):
    def __init__(self, violations: List[Violation]):
        self.violations = violations
        super().__init__(
            "payload rejected: " + "; ".join(f"[{v.category}] {v.pattern}" for v in violations)
        )


# ---------------- jail 档：能力域 RoE（2026-09-25，用户拍板） ----------------

@dataclass
class Cage:
    name: str
    canary_ip: str          # 笼内"假外联"落点（真实存在的 internal 网络成员）
    canary_hosts: tuple     # canary/靶 的 host 写法（去端口，含 127.0.0.1=笼自身回环）
    record: dict            # cage.json 原文（含 container_id / ip）


def _host_only(u: str) -> str:
    return external_host(u).split(":")[0]


def load_cage(path: str) -> Cage:
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"jail 档拒绝：cage 描述符不存在 {p}（先 engine.jail_ctl up）")
    r = json.loads(p.read_text(encoding="utf-8"))
    return Cage(name=r["name"], canary_ip=r["canary_ip"],
                # 只认笼坐标：回环刻意不列（127.0.0.1 属宿主，见 check_payload 的 jail 分支）
                canary_hosts=(_host_only(r["canary_ip"]), _host_only(r["ip"])),
                record=r)


_HOST = re.compile(r"https?://([^/\s'\"<>]+)", re.I)


def external_host(url: str) -> str:
    m = _HOST.match(url) if url.lower().startswith(("http://", "https://")) else None
    return m.group(1) if m else url


def verify_cage_isolation(c: Cage, timeout: int = 12) -> dict:
    """机器自检：不信词表、信 namespace。任何一项不过 → RuntimeError（fail-closed）。
    结果（含失败输出原文）由调用方落盘当证物——jail 档的门票是可复验的物证。"""
    cid = c.record["container_id"]

    def ex(args: List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", "exec", cid, *args], capture_output=True,
                              text=True, timeout=timeout)

    proof: dict = {"container": cid[:12]}
    tok = ex(["cat", "/app/.aegis_jail_token"])
    rec_token = str(c.record.get("token") or "")
    if not rec_token:
        raise RuntimeError("cage 自检失败：cage.json 无有效 token 字段（门票不可自证，拒 jail 档）")
    if tok.stdout.strip() != rec_token:
        raise RuntimeError("cage 自检失败：笼内门票 token 与 cage.json 不符"
                           "（笼被重建或描述符被换——jail 档立即失效）")
    proof["token_match"] = True
    m = subprocess.run(["docker", "inspect", "-f",
                        "{{json .HostConfig.Tmpfs}}", cid],
                       capture_output=True, text=True, timeout=timeout)
    if "/app" not in m.stdout:
        raise RuntimeError(f"cage 自检失败：/app 非 tmpfs -> {m.stdout[:120]}")
    net = subprocess.run(["docker", "network", "inspect", c.record["network"],
                          "-f", "{{.Driver}}|{{.Internal}}"],
                         capture_output=True, text=True, timeout=timeout)
    if net.stdout.strip() != "bridge|true":
        raise RuntimeError(f"cage 自检失败：网络 {c.record['network']} 非 internal -> {net.stdout[:120]}")
    egress = ex(["php", "-r", 'var_dump((bool)@stream_socket_client("tcp://1.1.1.1:80",$e,$s,3));'])
    if "bool(true)" in egress.stdout:
        raise RuntimeError("cage 自检失败：笼内可达公网 1.1.1.1")
    # 宿主 home 不可见（路径由门票 staging 字段推导，换机器/换用户不空转）
    segs = [s for s in str(c.record.get("staging", "")).split("/") if s]
    host_home = "/" + "/".join(segs[:2]) if len(segs) >= 2 else "/root"
    if "VISIBLE" in ex(["sh", "-c", f"ls {host_home} >/dev/null 2>&1 && echo VISIBLE"]).stdout:
        raise RuntimeError(f"cage 自检失败：笼内可见宿主目录 {host_home}")
    # 横向面探针（评审 2026-09-25 Q2）：网关即宿主本机，封它的 iptables 规则**未持久化**，
    # 重启后横向面悄悄张开——门票必须每次自验，否则"横向面归零"退化成历史事实。
    gw = ".".join(c.canary_ip.split(".")[:3]) + ".1"
    if "bool(true)" in ex(["php", "-r",
            'var_dump((bool)@fsockopen("' + gw + '",22,$e,$s,2));']).stdout:
        raise RuntimeError(
            f"cage 自检失败：笼内可达宿主网关 {gw}:22（sshd）——网关封锁规则已失效"
            "（重启即失），请重下 bench/d6-jail.md 的 iptables 规则，或改用 strict 档")
    proof["lateral_blocked"] = True
    canary = ex(["php", "-r",
                 'var_dump((bool)@fsockopen("' + c.canary_ip + '",80,$e,$s,2));'])
    if "bool(false)" in canary.stdout:
        raise RuntimeError("cage 自检失败：canary 假外联落点不可达（改写将无去向）")
    proof["egress_blocked"] = True
    proof["host_home_invisible"] = True
    proof["canary_reachable"] = True
    return proof


_RELDOT = re.compile(r"\.\./|\.\.%2f", re.I)
# 越笼写原语：jail 档专属底片——strict 词表只拦"超级全局→落盘"链，
# 常量路径写（file_put_contents('/root/…')）必须在这里显式覆盖。
_WRITE_PRIM = re.compile(
    r"\b(?:file_put_contents|fopen|fwrite|move_uploaded_file|touch|mkdir|rename|unlink|symlink|require|include)"
    r"(?:\s*\(\s*)?\\?['\"]?(/(?!app/)|[^'\"()\s]*\.\./)", re.I)
# 落盘进 /app 的 .php（jail 档允许的能力本身）——豁免 _WRITE_PRIM 的绝对路径命中
_IN_CAGE_PHP = re.compile(r"/app/[\w./-]*\.ph(?:p|tml|t|ar|3|4)\b", re.I)
# 一切特权/系统目录绝对封锁（即使笼内该路径不存在）
_SYSABS = re.compile(r"(?<![\w./])/(?:etc|boot|usr|var|root|proc|sys|bin|sbin|lib|home|opt)(?![\w.-]|/self/)", re.I)



def check_payload(text: str, collector_port: int = 30010,
                  mode: str = "strict", cage: Optional[Cage] = None) -> List[Violation]:
    """strict=内容禁忌（缺省）。jail=能力域：落盘/破坏性写在本笼内放行，
    但 off-cage 写与资源耗尽与第三方外联仍然违规。"""
    found: List[Violation] = []
    jail = mode == "jail" and cage is not None
    if mode == "jail" and cage is None:
        found.append(Violation("jail_gate", "jail without cage", "mode=jail 必须给已验证的 Cage"))
    for category, patterns in CATEGORY_PATTERNS.items():
        if jail and category in ("destructive_write", "persistence"):
            # 能力域：笼内落盘/破坏放行，只查越笼写法（strict 走下面的全量词表）
            for pat in patterns:
                m = pat.search(text)
                if m and _is_offcage(text, m):
                    found.append(Violation(category + "_offcage", pat.pattern, m.group(0)[:80]))
            if category == "persistence":
                for wm in _WRITE_PRIM.finditer(text):
                    found.append(Violation("persistence_offcage", "_WRITE_PRIM", wm.group(0)[:80]))
            continue
        for pat in patterns:
            m = pat.search(text)
            if m:
                found.append(Violation(category, pat.pattern, m.group(0)[:80]))
    for m in _URL.finditer(text):
        u = m.group(0)
        if _LOCAL_EGRESS.match(u):
            if jail:
                # 评审 P0（2026-09-25）：发射机在宿主上，jail 档出现回环 URL 打的是
                # **宿主服务**（A 靶 :8081 / 125B :30000 / Console :8000），不是笼。
                # 笼走 internal 桥 IP，永远不是 127.0.0.1 —— 故 jail 下一律拒。
                found.append(Violation("outbound_exploit",
                                       "loopback URL in jail mode = host service", u[:80]))
                continue
            port = re.search(r":(\d+)", u.split("/", 3)[2] or "")
            if port and int(port.group(1)) not in (collector_port,):
                found.append(Violation("outbound_exploit", "non-collector port", u[:80]))
        elif jail and _is_cage_origin(u, cage):
            continue                      # 假外联落点：canary 记账即证据
        else:
            found.append(Violation("outbound_exploit", "external URL", u[:80]))
    if jail:
        sm = _SYSABS.search(text)
        if sm:
            found.append(Violation("escape_syspath", "_SYSABS", sm.group(0)[:80]))
        for wm in _WRITE_PRIM.finditer(text):
            tail = text[wm.end(): wm.end() + 80]
            if _IN_CAGE_PHP.search(tail):
                continue                  # 落盘进 /app 的 .php = 能力域本身
            found.append(Violation("escape_write_prim", "_WRITE_PRIM", wm.group(0)[:80]))
    return found


def _path_offcage(p: str) -> bool:
    p = p.split("?")[0]
    if p == "/app" or p.startswith("/app/"):
        return False
    return bool(p.startswith("/")) and not re.match(
        r"/(dev/(null|stdin|stdout)|proc/self/)", p)


def _is_offcage(text: str, m) -> bool:
    """命中点同段（截到 ; & | ` 换行）内做围栏：引号路径与裸绝对路径分开判。
    文本栅栏是纵深防御——真边界是 namespace：发射端 base 锁死笼坐标，
    宿主靶机在另一个网络里，payload 写什么物理上都够不到。"""
    seg = text[m.start():]
    for stop in (";", "&", "|", "`", "\r\n", "\n", " HTTP/"):
        i = seg.find(stop)
        if i >= 0:
            seg = seg[:i]
    seg = seg[:200]
    if re.search(r"(?i)\b(?:rm|mv|touch|cp|dd|tee)\b[^;|&]*?(?<![\w.])/(?!app/)\w", seg):
        return True
    for qm in re.finditer(r"['\"]([^'\"]+)['\"]", seg):
        q = qm.group(1)
        if q.startswith(("http", "https", "ftp")) or "://" in q:
            continue          # scheme 形态归外联闸管，路径闸不双标
        if _path_offcage(q) or "../" in q:
            return True
    bare = re.search(r"(?<!['\"/\w])/(?!/)[\w./-]+", seg)
    if bare and _path_offcage(bare.group(0)) and "://" not in seg[max(0, bare.start() - 8):bare.start()]:
        return True
    return bool(_RELDOT.search(seg))


def _is_cage_origin(url: str, cage: Cage) -> bool:
    return external_host(url).split(":")[0] in cage.canary_hosts


def rewrite_external_to_canary(text: str, cage: Cage) -> "tuple[str, List[str]]":
    """jail 档预处理器：第三方 URL → canary 假外联落点。
    只回替换后的文本——原意图由返回值 intents 走账本/拒案留痕渠道。
    不把意图头拼进报文本身：表单体尾追头会污染末字段值（2026-09-25 实弹
    破案：canary 已收戳但应用报 502，因 URL 值被灌进 \r\nX-Aegis 头）。"""
    intents: List[str] = []

    def _sub(m):
        u = m.group(0)
        host = external_host(u)
        if _LOCAL_EGRESS.match(u) or _is_cage_origin(u, cage):
            return u
        intents.append(u)
        tail = u.split(host, 1)[1]
        return f"http://{cage.canary_ip}{tail}"

    out = _URL.sub(_sub, text)
    return out, intents


def assert_allowed(text: str, collector_port: int = 30010,
                   mode: str = "strict", cage: Optional[Cage] = None) -> None:
    violations = check_payload(text, collector_port, mode=mode, cage=cage)
    if violations:
        raise PolicyViolation(violations)
