# Aegis 历史证据与复算入口

这里汇集已公开的运行和模型汇总证据。数值由 [claims.yaml](claims.yaml)
登记，使用下面的命令核对仓内汇总数据：

    python bench/train/assert_claims.py --public
    python bench/train/check_docs_numbers.py

完整原始流量与封存材料仍在原实验环境；公开核对会把缺少原始材料的断言列入
not_evaluated。本文的模型成绩和运行收据来自提交版历史记录，当前开发分支尚未
在 DGX Spark 上重新测时延或重训权重。

| 证据 | 仓内出处 | 能直接核对什么 |
|---|---|---|
| JEV v3 混合域 holdout | [评估汇总](bench/results/three_arm/v3_mixed.json) | n=4,244，准确率 99.55%；与封存 B 卷分开登记 |
| JEV v3 封存 B 卷 | [B 卷汇总](bench/train/results/d7_b/v3_eval_b_clean.json) | n=7,266，准确率 99.92%；不将同形请求面称为新攻击泛化 |
| JEV v3 训练 | [训练汇总](bench/results/judge_training/v3_summary.json)、[数据统计](dataset/judge_v3_mix/stats.json) | 17,403 条训练、4,244 条 holdout；LoRA r=16、alpha=32、dropout=0.05 |
| Spark 推理 | [Spark 汇总](bench/results/judge_latency/spark_v3_single_shot.json) | 单发 median 80.8 ms、p95 118.5 ms、12.17 条/秒；与训练机对账 60 条，判定零翻转 |
| 主闭环 | [攻击复测](evidence/main-run-run-20260927-140612-399b/verification.json)、[收据](evidence/main-run-run-20260927-140612-399b/evidence-receipt.json) | 攻击重放 0/2 命中、正常业务通过、11 项功能测试通过、环境恢复 |
| 红方经验复用 | [首次探测](evidence/red-library/hunt_zeroshot.json)、[经验库探测](evidence/red-library/hunt_library.json) | 同一目标由 21 次请求降至 3 次；经验库命中有来源记录 |
| 自修补 Skill | [调用记录](evidence/skill-run-run-20260928-155647-3290/capture.json) | 真实 CLI 发起并得到 verified_restored，17 项收据条件通过 |
| 提交版公开验收 | [r4 验收记录](VALIDATION.json) | 当时 73 项测试、16 项子测试、39 项直接数值核对 |

JEV 只做攻/不攻分类与弃权，不判断漏洞类别，也不决定补丁是否通过。
新收集的对抗样本进入审核队列；候选数据并不意味着新版模型已经训练成功。
判官当前开发分支的服务压力测试、GPU 共存时延和两目标闭环，需对应独立实机记录。

当前代码的 CPU 回归结果与未实现能力写在
[开发验收](docs/development/regression-final.json)和[硬化记录](docs/development/HARDENING.md)。
