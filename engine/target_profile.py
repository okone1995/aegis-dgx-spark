"""Target profile 加载层（T11/S1）—— fork 版（带 surface 与 deploy_argv）。

引擎历史上把目标写死为 target/edu-lite（13+ 处）。本模块把"目标"变成数据：
targets/<name>/profile.yaml 声明目标的路径、门禁命令、实例与语料映射，
引擎一律通过 load()/active() 取值，不再 import edu-lite 字面路径。

设计约束（与 demo 仓红线一致）：
- 缺 profile / 缺字段 ⇒ 立刻抛 TargetProfileError（可见失败，绝不静默回退 edu-lite）；
- 默认目标 = edu-lite（与现状行为一致，回归零变化），由 AEGIS_TARGET 覆盖；
- 本模块不解释 gate 命令的语义，只做加载与展开；执行仍在各阶段模块。
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
TARGETS_DIR = ROOT / "targets"
DEFAULT_TARGET = "edu-lite"
LEGACY_ASSET_TARGET = "edu-lite"  # 唯一允许回退仓内 plugins/ 的目标
ENV_OVERRIDE = "AEGIS_TARGET"


class TargetProfileError(RuntimeError):
    """profile 缺失/不合法。调用方必须让这个错误可见，不得回退默认目标。"""


@dataclass(frozen=True)
class Instance:
    name: str
    base: str
    label: str = ""
    surface: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TargetProfile:
    name: str
    kind: str
    paths_root: Path
    canonical_sources: List[Path]
    deploy_cmd: str
    runtime_dir: Path
    syntax_gate: str
    functional_test_cmd: Optional[str]
    benign_traffic_cmd: Optional[str]
    instances: Dict[str, Instance]
    corpus_keys: Dict[str, List[str]]
    marker_semantics: str
    display: str = ""
    _raw: dict = field(default_factory=dict, repr=False)

    @property
    def primary_source(self) -> Path:
        if not self.canonical_sources:
            raise TargetProfileError(f"profile {self.name}: canonical_sources 为空")
        return self.canonical_sources[0]

    def expand_syntax_gate(self, file: Path, php: Optional[str] = None) -> List[str]:
        """把 gates.syntax 展开为 argv（{file} 占位替换）。

        注意：shlex 在 POSIX 模式下会把反斜杠当转义符，Windows 路径会被吃掉分隔符，
        所以先归一成正斜杠再切分（php/python/node 都接受正斜杠）。

        `php` 参数：profile 里写裸令牌 `php`（edu-lite/miniledger 的形态）时，用调用方
        传进来的解释器路径替换 argv[0]，与 verify.functional_tests 替换 `python` 令牌同一
        约定。为什么必须能替换：改造前 runloop 用的是 `[a.php, "-l", file]`（--php 直接进
        argv）；去耦合后若只认 PATH 上的裸 php，靶机上 php 不在 PATH（DGX 是
        ~/tools/php/php）就会把补丁轮整轮崩掉——那是降级，不是数据化。
        profile 写了绝对路径或 ${...} 展开成路径的，一律不替换（显式压过推断）。
        """
        parts = shlex.split(self.syntax_gate.replace("\\", "/"))
        if php and parts and parts[0] == "php":
            parts[0] = str(php)
        file_posix = str(file).replace("\\", "/")
        return [file_posix if p == "{file}" else p for p in parts]

    def deploy_argv(self, instance: str = "a") -> List[str]:
        """把 paths.deploy_cmd 展开为 argv 并附加实例名（同样先归一反斜杠）。"""
        return shlex.split(self.deploy_cmd.replace("\\", "/")) + [instance]

    def plugins_overlay(self) -> Path:
        """新目标自带插件资产目录（payloads/markers/patch-template 随目标走）。"""
        return TARGETS_DIR / self.name / "plugins"

    def plugin_dir(self, cls: str, default_root: Path) -> Path:
        """按目标解析某类的插件目录：overlay 优先；**只有 edu-lite 允许回退**仓内资产。

        为什么回退仅限 edu-lite：仓内 plugins/<class>/ 的载荷、markers、补丁模板都是
        edu-lite 专用的。若新目标缺自己的资产却静默用 edu-lite 的，就会出现
        "打错路由、用错 marker" 的假结果 —— 这比直接失败危险得多（T11/S1f 实测踩过：
        攻击阶段拿着 /news/search 的载荷打 MiniLedger，判成 attack_not_exploited）。
        """
        overlay = self.plugins_overlay() / cls
        if overlay.is_dir():
            return overlay
        if self.name == LEGACY_ASSET_TARGET:
            return default_root / cls
        raise TargetProfileError(
            f"目标 {self.name!r} 未提供 {cls!r} 类插件资产（期望目录 {overlay}）；"
            f"拒绝回退到 {LEGACY_ASSET_TARGET!r} 的载荷/markers/模板")

    def instance(self, name: str = "a") -> Instance:
        try:
            return self.instances[name]
        except KeyError:
            raise TargetProfileError(
                f"profile {self.name}: 无实例 {name!r}；可用: {sorted(self.instances)}") from None


_ENV_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


def _expand(value, ctx: str):
    """展开 profile 里的 ${ENV}；未设置的变量必须可见失败（不得留字面量继续跑）。

    为什么要有它：目标目录/解释器路径随环境变化，profile 不该把宿主绝对路径写进仓
    （本仓有红线：跟踪文件不得含宿主路径）。用 AEGIS_C_WORK / AEGIS_PHP_BIN 之类
    的操作员变量替代，未设置就报错而不是猜。
    """
    if not isinstance(value, str):
        return value
    left = _ENV_REF.findall(value)
    if left:
        missing = [t for t in left if os.environ.get(t[2:-1]) is None]
        if missing:
            raise TargetProfileError(
                f"profile {ctx}: 环境变量未设置 {missing}（请在 agent 任务外提供）")
        value = os.path.expandvars(value)
    return value


def _require(mapping: dict, key: str, ctx: str):
    if key not in mapping or mapping[key] in (None, ""):
        raise TargetProfileError(f"profile {ctx}: 缺必填字段 {key!r}")
    return mapping[key]


def load(name: Optional[str] = None) -> TargetProfile:
    """加载 targets/<name>/profile.yaml。缺文件/缺字段都抛 TargetProfileError。"""
    name = name or os.environ.get(ENV_OVERRIDE) or DEFAULT_TARGET
    pdir = TARGETS_DIR / name
    pfile = pdir / "profile.yaml"
    if not pfile.is_file():
        raise TargetProfileError(
            f"未找到目标 profile: {pfile}（AEGIS_TARGET={name!r}）。"
            f"已注册目标: {available()}")
    try:
        import yaml  # 引擎其余部分同样依赖 PyYAML
        raw = yaml.safe_load(pfile.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都必须可见
        raise TargetProfileError(f"profile {name}: 解析失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise TargetProfileError(f"profile {name}: 顶层不是映射")

    paths = raw.get("paths") or {}
    gates = raw.get("gates") or {}
    root = (ROOT / _expand(_require(paths, "root", f"{name}.paths"), name)).resolve()
    canon = [root / s for s in (_require(paths, "canonical_sources", f"{name}.paths"))]
    runtime_dir = root / "runtime"
    if paths.get("runtime_dir"):
        runtime_dir = ROOT / paths["runtime_dir"]

    insts: Dict[str, Instance] = {}
    for iname, cfg in (raw.get("instances") or {}).items():
        insts[iname] = Instance(
            name=iname,
            base=_require(cfg, "base", f"{name}.instances.{iname}"),
            label=cfg.get("label", ""),
            surface=dict(cfg.get("surface") or {}),
        )

    return TargetProfile(
        name=name,
        kind=_require(raw, "kind", name),
        display=raw.get("display", ""),
        paths_root=root,
        canonical_sources=canon,
        deploy_cmd=_expand(_require(paths, "deploy_cmd", f"{name}.paths"), name),
        runtime_dir=runtime_dir,
        syntax_gate=_expand(_require(gates, "syntax", f"{name}.gates"), name),
        functional_test_cmd=_expand(gates.get("functional_test"), name),
        benign_traffic_cmd=_expand(gates.get("benign_traffic"), name),
        instances=insts,
        corpus_keys={k: list(v) for k, v in (raw.get("corpus", {}).get("keys", {}) or {}).items()},
        marker_semantics=raw.get("marker_semantics", "error_echo"),
        _raw=raw,
    )


def available() -> List[str]:
    if not TARGETS_DIR.is_dir():
        return []
    return sorted(p.name for p in TARGETS_DIR.iterdir()
                  if p.is_dir() and (p / "profile.yaml").is_file())


def active() -> TargetProfile:
    """当前激活目标（默认 edu-lite；AEGIS_TARGET 可覆盖）。"""
    return load()
