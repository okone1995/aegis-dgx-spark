# -*- coding: utf-8 -*-
"""C5 修复的静态门禁:详情抽屉不得遮挡右栏(≤1560px)。

真机大屏是三列 grid(300 / 1fr / 330),右栏 `.colR` 承载风险面板。
抽屉是 position:fixed 的 440px,打开时会盖住右栏。修法:
宽度收敛到 --dw 单一变量,并在打开时把 .colR 左推同样距离(body.drawer-open)。
本门禁锁三件事:变量存在、窄视口内收、开关成对(classList 增删)。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "demo" / "index.html").read_text(encoding="utf-8")


def test_drawer_width_is_single_variable():
    assert "--dw:440px" in HTML, "抽屉宽度应定义为 --dw 变量"
    assert re.search(r"#drawer\{[^}]*width:var\(--dw\)", HTML), \
        "#drawer 宽度必须引用 --dw(避免多处硬编码 440px)"


def test_narrow_viewport_shrinks_drawer_and_pushes_rail():
    m = re.search(r"@media\(max-width:1560px\)\{(.*?)\n  \}", HTML, re.S)
    assert m, "应有 ≤1560px 的媒体查询"
    block = m.group(1)
    assert "--dw:340px" in block, "窄视口必须把 --dw 收小"
    assert re.search(r"body\.drawer-open \.colR\{transform:translateX\(calc\(-1 \* var\(--dw\)\)\)\}", HTML), \
        "抽屉打开时右栏(.colR)必须被推开同样距离,而不是被盖住"


def test_drawer_open_close_toggle_body_class():
    assert re.search(r"document\.body\.classList\.add\(\"drawer-open\"\)", HTML), \
        "drawerOpen 必须给 body 加 drawer-open"
    assert re.search(r"document\.body\.classList\.remove\(\"drawer-open\"\)", HTML), \
        "drawerClose 必须移除 drawer-open(否则右栏被永久推开)"
