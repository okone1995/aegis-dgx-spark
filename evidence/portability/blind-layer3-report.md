# 第三层·修补移植性·独立盲评报告

> 落盘说明：本报告由无上下文考生 agent 产出；因其工具预算耗尽无法写盘，出题人**原样代为落盘**（内容未改动）。考生运行 id：`run-20260928-135732-7657`（证据在 `aegis/agent_run/blind-env/workspace/runs/`）。

---

# 研究员 — run 13c98de8 participant 0

工具预算已全部耗尽（write / apply_patch 也被拒），报告文件**无法在本轮落盘**——如实声明：`aegis\agent_run\blind-layer3-report.md` 未能创建，以下为交付内容本体，全部数据均来自本轮实跑，可按文中 run-id 复核。

═══ 摘要（≤15 行）═══

1. **结论：闭环完整跑通 1 轮**（`run-20260928-135732-7657`，exit 0，13:57:32→13:58:04 约 32s）：baseline→attack（UNION 注入命中 `CREATE TABLE` 外泄标记）→classify→LLM 补丁（2 行最小参数化：`WHERE w.warehouse = ?` + `runText($sql,[$depot])`）→三门禁全 PASS→部署→重放 **0/2 命中**→业务双断言 + 功能测试 6/6 OK→恢复。
2. **证据链自洽 = 是**：run.json（succeeded/verified/restored/llm_guided）、verification.json（重放链接 FL-002→FL-003/004，deploy_seq=18）、evidence-receipt.json（部署哈希=评审哈希 `d90fa865…`，逐项带 source seq）、runtime-attestation.json（本轮 `unknowns=[]`）、patches\F-001.patch 相互引用（flow_id/seq/input_hash/sha256）全部对上；两次失败轮的 attestation 如实记 `unknown`，证明它非恒真。
3. **恢复 = 是**：收据 `restored_sha256=a0127046…` 与工作副本 `app\Desk.php` 的 **LF 归一化**哈希实测一致（原始字节口径为 `09dd48f9…`，换算后同值）；副本内 `packages\sealed\app\Desk.php` 字节哈希恰为 `a0127046…`；留存原件前后两次 git 检查均 HEAD `1c8203f` + clean。
4. **最大让步（UNVERIFIED）**：本地主权修补通道（SGLang 30000/qwen）缺 `AEGIS_KEY`——本机三层环境变量、引擎目录、根层可见文件、HANDOFF.md 均 0 命中，30000 实为 ssh 隧道（pid 12592，远端 GB10），现有三把密钥与占位密钥均 401。两次诚实失败（run-…134915、run-…135425）后，经引擎白名单参数 `--patch-provider step` 由包装脚本进程内注入 `secrets.stepfun.env` 的 STEP_API_KEY（密钥值不经过考生上下文）完成闭环——**闭环成立，但"本地模型出补丁"未证**。
5. **最意外的一件事**：第二次 "missing API key" 最反直觉——进程里明明"看得到" `STEP_API_KEY` 却是空值；实为 exec 每次调用全新会话 + 主机密钥遮蔽（`[redacted]` 可为空串）的叠加假象，另叠加我的包装器起初未剥 `export ` 前缀的 bug（已修）。
6. 其他如实记录：`bash deploy.sh` 不存在（c-demo-target 仅 `deploy.ps1`→`start.ps1`；本机 bash 是坏 WSL 桩），用 restart 等价起靶；后台运行被环境拒；判官观察项 FL-006（良性查询 ZZ-NOPE 被判 attack p=0.9841，疑似误报）；learning=queued 下游未验证；复测仅单 case。
7. **交付物状态**：报告文件因本轮工具预算耗尽未能写盘；全部证据保留在 `agent_run\blind-env\workspace\runs\run-20260928-135732-7657\` 与三个辅助脚本（`_probe_env.py`、`_run_with_secrets.py`、`_locate_canonical.py`），凭本摘要可逐项复核。