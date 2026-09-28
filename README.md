# Aegis · 受控 RSI，让安全智能体具备可验证的自修补能力

保险等敏感业务同时需要防止数据泄漏和保持正常服务。Aegis 在授权靶场把红方取证、JEV 独立流量分类、模型修补、攻击与业务复测连成可审计工作流，再通过 Skills 交给宿主智能体调用。

| 先看什么 | 包内入口 |
|---|---|
| **四分钟最终演示** | [aegis-final-demo-20260929.mp4](workspace/video/aegis-final-demo-20260929.mp4) |
| **44 秒真实技能执行，中文旁白** | [aegis-skills-real-execution-20260929.mp4](workspace/video/skills-live/aegis-skills-real-execution-20260929.mp4) |
| RSI 路演 PPT（9 页） | [aegis-dgx-spark-judge-pitch-rsi-20260929.pptx](workspace/ppt-final/aegis-dgx-spark-judge-pitch-rsi-20260929.pptx) |
| 十日谈 | [docs/ten-days.md](docs/ten-days.md) |
| 已验能力与边界 | [docs/SKILLS-SUPPORT.md](docs/SKILLS-SUPPORT.md) / [RELEASE.md](RELEASE.md) |

RSI 的改进有三种证据：红方同一已知目标从 21 次请求到人审入库后下一轮 3 次命中；蓝方实际修改代码，使同一攻击重放 0/2 且正常业务通过；JEV v3 已训练部署，真实分歧和新流量进入下一轮候选材料。**本轮下一版权重尚未训练，未声称未知攻击泛化。**

主运行 `run-20260927-140612-399b` 的 16 件证据在 `evidence/main-run-run-20260927-140612-399b/`。新技能运行 `run-20260928-155647-3290` 的实际命令与结果在 `evidence/skill-run-run-20260928-155647-3290/`；返回 verified_restored，17/17 收据核验通过，五条新候选入队。两轮记录不拼成一轮。

Spark GB10 部署本地 125B NVFP4/SGLang 修补模型和 JEV 4B bf16 推理；v3 LoRA 训练在本机 5090。StepFun 云端复核候选 diff。JEV 判断攻/正常/弃权，不能裁决补丁生效，p_attack 不是业务风险概率。

从解压目录运行公开验收（Python 与 requirements.txt 中的依赖需要预装）：
```text
python tools/verify_clean_export.py .
```
验收真正检查视频/PPT、16 件证据、结果文件和全部交付文件哈希，并运行公开离线子集与四个技能桥接测试。它不代表私有资产/GPU集成测试全绿。现场 LIVE 另需后台、授权靶场、模型与操作员 scope；公开包不含模型权重、凭据、完整靶站和可直接使用的载荷库，不能在此包裸跑完整攻击链。

完整来源和文件哈希见 FINAL-BUILD-MANIFEST.json；解压验证结果见 VALIDATION.json。文本证据是脱敏副本，内部部署哈希保留原始运行的字节口径。未获 NVIDIA-Verified。
