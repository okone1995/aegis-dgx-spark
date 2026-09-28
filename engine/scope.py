"""scope.yaml 校验 — 运行前强制检查，不在授权清单内即拒绝（SPEC §7/§15）。

这是治理的机器执行层：不是 README 承诺，是代码。
"""
from __future__ import annotations

import ipaddress
from typing import List, Literal, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, Field


class ScopeError(Exception):
    pass


class Target(BaseModel):
    name: str
    base_url: str
    bind: Literal["local-only", "any"] = "local-only"


class Scope(BaseModel):
    targets: List[Target]
    allowed_classes: List[str]
    forbidden_actions: List[str] = Field(
        default_factory=lambda: [
            "destructive_write", "persistence", "outbound_exploit", "dos", "worm",
        ]
    )
    max_rounds: int = Field(default=5, ge=1, le=5)
    collector_port: int = 30010  # payload 回显允许的唯一本地采集端口

    @classmethod
    def from_yaml(cls, path) -> "Scope":
        with open(path, encoding="utf-8") as fh:
            return cls.model_validate(yaml.safe_load(fh))

    # ---------- 校验 ----------
    def target(self, name: str) -> Target:
        for t in self.targets:
            if t.name == name:
                return t
        raise ScopeError(f"target '{name}' not in scope")

    def check_url(self, url: str, target: Optional[Target] = None) -> None:
        """local-only 目标只允许回环/内网地址。"""
        t = target
        if t is None:
            host_all = {urlparse(tt.base_url).hostname for tt in self.targets}
            h = urlparse(url).hostname
            if h not in host_all:
                raise ScopeError(f"host '{h}' not in scope targets")
            t = next(tt for tt in self.targets if urlparse(tt.base_url).hostname == h)
        if t.bind == "local-only" and not _is_local(urlparse(url).hostname or ""):
            raise ScopeError(f"target '{t.name}' is local-only, host not loopback/private: {url}")

    def check_class(self, cls_name: str) -> None:
        if cls_name not in self.allowed_classes:
            raise ScopeError(f"vuln class '{cls_name}' not allowed in scope")

    def check_action(self, action: str) -> None:
        if action in self.forbidden_actions:
            raise ScopeError(f"action '{action}' forbidden by scope")


def _is_local(host: str) -> bool:
    if host in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # 非 IP 字面量且非 localhost，保守拒绝
    return ip.is_loopback or ip.is_private
