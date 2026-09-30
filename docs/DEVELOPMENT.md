# Aegis 开发环境

此目录是新目录，`engine/` 是 Aegis 引擎的唯一源码；`workspace/` 只保存运行时生成物，不作为源码或配置的权威副本。模型权重保存在仓库外，避免进入源码提交。保留的 r4 媒体和证据是历史基线，用于对照，不代表当前代码、当前靶场状态或本次测试结果。

## 安装

支持 Windows 和 Linux，使用 Python 3.12。锁文件固定了 `.venv` 中已安装的 CPU 开发依赖；不安装 GPU 框架或模型权重。

Windows PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
.\.venv\Scripts\python.exe tools\doctor.py --offline
```

Linux/macOS shell：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python tools/doctor.py --offline
```

`--offline` 只检查锁文件列出的开发依赖；不检查靶场、模型服务、凭据或技能运行配置，因此无 GPU 的开发机可以通过此项基础检查。完整自检使用 `python tools/doctor.py`，以 JSON 报告依赖、四个 `aegis-*` skill 入口、target profile 引用和解释器，以及本地目标登记表。缺少项目配置时状态为 `needs_configuration`；Doctor 只读，不联网，也不启动模型或靶场。

## 回归测试

以下回归集不需要真实靶场或模型，HTTP 测试仅启动自己的临时 loopback 服务；
不可达地址测试在 transport 层注入连接失败，不探测固定端口。

运行同一命令于 Windows / Linux：

    python -m pytest -q -rs --import-mode=importlib tests skills/aegis-hunt/tests skills/aegis-self-repair/tests skills/aegis-jevtrain/tests skills/aegis-evolve/tests

--import-mode=importlib 允许多个 skill 的同名 test_bridge.py 共存。
GitHub Actions 使用同一全量 CPU 清单；私有原始数据和平台依赖不足的项会明确 skip。
现场攻击、PHP 部署和 GPU 推理不在这条命令的验收范围。

公开汇总证据可单独验证：

    python bench/train/assert_claims.py --public

这个模式列出 not_evaluated 的私有 computed 条目，不把它们记成通过。
完整原始证据在操作员环境配置后，省略 --public 执行严格验算。

## 本轮交付与下一步

见 [HARDENING.md](development/HARDENING.md)：
已完成的并发、服务和开发环境改动，以及尚未实现的多目标 Skill 和多轮修补验收。
本地最终测试结果单独记录为 development/regression-final.json。
根目录的 VALIDATION.json、FINAL-BUILD-MANIFEST.json 属于 r4 历史发布，不能验当前开发改动。
