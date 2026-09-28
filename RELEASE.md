# Final release · 2026-09-29 local time

R2 完善项目主页：项目定位、六大优势、系统架构、RSI 流程、JEV 技术与成绩、Skills 复用、代码结构、快速开始及部署说明；新增项目横幅与真实大屏截图。验收执行交付副本的 CLI，并支持正常 Git 克隆和仓内虚拟环境。

提交视频：workspace/video/aegis-final-demo-20260929.mp4（四分钟）。新技能短片：workspace/video/skills-live/aegis-skills-real-execution-20260929.mp4（44.233 秒，有旁白，等待 4× 加速）。PPT：workspace/ppt-final/aegis-dgx-spark-judge-pitch-rsi-20260929.pptx。十日谈：docs/ten-days.md。所有实际完整哈希与源文件版本均见 FINAL-BUILD-MANIFEST.json。

## 运行证据

- 历史主演示 run-20260927-140612-399b：16 件收据/事件/部署身份/攻击和业务复测/补丁证据已随包交付。
- 新真实 skill run-20260928-155647-3290：verified_restored；llm_guided；17/17 证明项；五条新候选、四条重复。脚本、实际命令日志与后台工件随包交付；完整原始录屏留在本机。
- 红方经验库：同一已知目标的两个独立轮次 21→3，附两份原始结果和操作员写回记录；不表示未知目标泛化或权重提高。
- C 独立结果：支持经过适配的修补效果；严格 oracle 工件、54 项业务规程/reset 尚未齐。最新独立轮由 StepFun 生成补丁，不能主张该轮是本地模型修补。

## 来源冻结

主仓的 demo/console/harness 与现场主链、fork 的 engine/skills/tools/tests 能力线按白名单合并。逐文件记录源路径、源 SHA256、交付 SHA256、字节数；base commit 之外的修改由 source_sha256 明确标识。脱敏后副本与录屏现场源码并非逐字节相同，不作虚假源码身份承诺。旧快照 aegis-review 保留历史；本包才是正式本地交付物。

## 模型与门禁

JEV v3 adapter safetensors 在 Spark 的 SHA256：01b21886b6f65bee45a9a058ba8059115f52b9ff0ee6604deda3d147dfb36842。部署 GB10；训练 5090；单发 v3 median 80.8 ms / p95 118.5 ms。线上弃权带 0.40–0.60，离线评估带 0.20–0.80，不能互引。没有 GGUF 实测时延。

攻击意图与打穿结果分开；标签审核与样本批准分开。导出与训练的质量门禁必须通过。v8/v9 禁止训练，下一版权重尚未训练。环境变量操作员闸门是意图/来源约束，不能声称拥有独立 OS 权限隔离。

## 验收与边界

已运行能力线：75 passed / 16 subtests passed。公开候选包：73 passed / 2 skipped / 16 subtests passed；两项跳过需要未交付的 judge_v0 和留存 B 语料。正式包和干净解压包另跑同一套件，结果记录于 VALIDATION.json。媒体丢失/损坏的负例登记于所有者验收报告。完整私有/GPU套件不在此验收声明中。

四个新 skills 的支持范围见 docs/SKILLS-SUPPORT.md。旧六个 secaudit 是内部组件，当前独立完整链仍未验收；secaudit-report 已从推荐流程退役。未实现任意目标自主修复、生产自动部署或完整训练升级循环。

官方上传由负责人手动完成；本包不包含尚未发生的上传/征文发布凭证。
