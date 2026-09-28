# Aegis GPT Image 架构图生成记录

- 模式：内置 image_gen 工具（GPT Image），非 CLI/API fallback。
- 最终资产：`aegis-architecture-gpt-v1.png`
- 用途：README 系统架构与路演材料。
- 核验重点：红蓝修补主链、JEV 独立分类、StepFun 云端 diff 复核、POC 复用与离线训练两条 RSI 回环。

## 初始生成提示词

```text
Use case: infographic-diagram.
Asset type: a polished system architecture illustration for the Aegis GitHub README and hackathon presentation. Create a brand-new landscape image, 16:9, high resolution. The diagram must look like a premium, carefully art-directed enterprise security product infographic, while communicating a real engineering architecture.

Project identity: Aegis, a multi-agent security system built around red-blue adversarial testing, verified code self-repair, a small JEV traffic judge and recursive self-improvement (RSI). Match its established visual identity: deep navy #08111F background, subtle emerald NVIDIA/DGX accent, coral red for red team, electric blue for code repair, cyan/teal for JEV, amber for review gates. Beautiful spacious grid, elegantly outlined rounded cards, restrained glow, crisp arrowheads and readable typography. Clean polished flat/soft-isometric hybrid; a small tasteful DGX compute icon is acceptable. Avoid decorative clutter.

Main title, exact: "AEGIS 系统架构"
Subtitle, exact: "红蓝对抗 · JEV 流量判官 · RSI 递归自我改进"

Composition and correct relationships:
1. A small top entry layer: "Web 大屏 / Agent Skills" connects to "FastAPI / DemoCase" with the caption "统一 run_id · 实时事件".
2. A large clearly bounded central area titled "NVIDIA DGX Spark · GB10". Inside, arrange the main code-repair lifecycle left to right in four readable cards:
   - coral: "红方自挖风险", secondary "Hunt · 探测 · POC"
   - blue: "蓝方生成补丁", secondary "本地 125B · NVFP4 / SGLang"
   - amber: "三道补丁门禁", secondary "范围 · 危险模式 · 异构复核"
   - green: "验证与恢复", secondary "攻击重放 · 正常业务 · 证据收据"
   Connect the cards in that exact sequence with clear arrows, and show the top orchestration coordinating this runtime.
3. Below the red card, a small "授权靶场 / Target Profile" node and a "真实请求 / 响应" junction. The real traffic branches independently to a small "规则检测" node and a prominent cyan judge card labeled exactly "JEV 流量判官", secondary "Qwen3.5-4B + LoRA", tertiary "攻击 / 正常 / 弃权". JEV is a parallel independent classifier. It must NOT be drawn as the patch review gate or a mandatory blocker in the code-patch sequence.
4. An amber cloud node OUTSIDE the DGX boundary labeled "StepFun 异构复核" connects only to the patch-gate card, with "候选 diff" on that connection. It is not a JEV provider.
5. A beautifully designed bottom feedback band titled "RSI：把本轮结果变成下一轮能力". Show two clearly separate feedback lanes:
   a) "POC 审核 → 经验库复用" loops back to the red-team card.
   b) "真实样本 → 标签审核 / 家族切分 → 离线训练 / 留存评估" has a dashed model-iteration arrow back to JEV, labeled "模型迭代流程". Traffic/JEV outputs and verification results feed this learning lane. Depict training as the governed improvement workflow, not an automatic live weight update.
The diagram should make the three memorable changes visible: attack experience improves, application code is repaired and verified, and traffic evidence supplies the classifier learning loop.

Text: use precisely the supplied Chinese/English labels, correct spelling and ample font sizes; reduce decorative microcopy rather than make text too small. The visual should remain legible at typical GitHub README width. No performance numbers, no fabricated metrics, no certification badges, no fake logos, no watermark, no extra unrelated components. Avoid crossed or tangled connectors. Prefer technical correctness and visual hierarchy over cramming in more detail.
```

## 架构校正提示词

```text
Use case: infographic-diagram. Edit the supplied Aegis architecture image. Preserve the deep navy palette, premium crisp rounded cards, red/blue/amber/cyan role colors, large Chinese typography, main title and overall polished visual quality. Correct only architecture relationships and associated layout; keep the image landscape 16:9 and spacious.

Required corrections:
- Top entry text becomes exactly "Web 大屏 / 自修补 Skill". Connect "FastAPI / DemoCase" DOWN to the "多智能体编排与任务调度（Aegis Runtime）" bar with a visible arrow.
- Re-layout the green dashed DGX boundary so it encloses the local runtime, four lifecycle cards, target traffic area, rule detector and JEV, but NOT the StepFun cloud. Reserve a narrow external column on the right of the DGX boundary for "StepFun 云端异构复核". Its ONLY connection is a clear two-way amber connector with the "三道补丁门禁" card, labeled "候选 diff / 复核结果". StepFun is not inside Spark and does not connect to JEV. Do not leave the cloud ambiguously inside the boundary.
- Keep the four-card lifecycle in this exact order: "红方自挖风险" → "蓝方生成补丁" → "三道补丁门禁" → "验证与恢复". Label the arrow from gates to verification "部署".
- Red team's arrow points DOWN to "授权靶场 / Target Profile" (the red team probes the target). Target points to "真实请求 / 响应"; traffic branches with separate arrowheads to "规则检测" and "JEV 流量判官". JEV remains an independent parallel classifier with text "Qwen3.5-4B + LoRA" and "攻击 / 正常 / 弃权". Do not connect JEV to the patch gate.
- In the bottom RSI band, the RED POC feedback arrow goes from "POC 审核 → 经验库复用" directly back UP to "红方自挖风险". It must not point to the target and must not originate from the JEV dashed feedback arrow.
- Lower learning lane should read "真实样本 → 标签审核 / 家族切分 → 离线训练 / 留存评估" and include small label "RTX 5090 · 离线训练". Connect JEV downward to that lane with label "判读与分歧"; connect verification results to the learning lane with one tidy thin connector. Then show a SEPARATE dashed cyan arrow from the offline training stage back UP to JEV labeled "模型迭代流程". Do not claim model outputs are ground truth or that weights update live.
- Remove the fictional rack server rendering. Use a subtle generic compact desktop compute icon with no invented product appearance. Use plain text "NVIDIA DGX Spark · GB10" for the boundary title rather than a generated brand logo.
- Remove unrelated decorative English slogans and the imitation logo. A neutral small footer may say "开放技能 · 可追溯证据 · 持续改进".

Ensure every arrowhead has the specified direction. Reduce extra microcopy if needed; exact core labels and readable clean layout take priority. No metrics or certification/endorsement claims.
```

## 部署边界与反馈校正

```text
Edit this architecture infographic, preserving all layout, visual style, colors, fonts, and existing correct text. Make exactly these THREE targeted corrections:
1) The StepFun card on the far right MUST be OUTSIDE the NVIDIA DGX Spark dashed green rectangle. Move ONLY the RIGHT EDGE of that dashed green rectangle leftward so it runs vertically in the gap BETWEEN the green "验证与恢复" card and the amber "StepFun" card. The right edge should be around 85% of image width, rather than around 98%. Shorten its top and bottom edges accordingly. Keep StepFun where it is. Remove the little desktop computer icon if it overlaps this new boundary. The cloud must visibly sit in the open dark background OUTSIDE the local compute box.
2) Fix the RED feedback wiring only. Delete the red upward "经验反馈" arrow that ends on "授权靶场". Draw a red connector that STARTS at the red "POC 审核 → 经验库复用" lane, travels around the LEFT side, and ends with a right-pointing arrowhead on the LEFT EDGE of the red "红方自挖风险" card. Label this connector "经验反馈". The separate vertical arrow from the red-team card DOWN to the target is ONE-WAY DOWN only, with no upward arrowhead.
3) In the blue "蓝方生成补丁" card, restore its secondary exact text "本地 125B · NVFP4 / SGLang". In the amber gate card, restore its secondary exact text "范围 · 危险模式 · 异构复核".
Do not change any other text or add other nodes. Especially preserve JEV, its independent traffic branch, and the separate downward data arrow and upward dashed model-iteration arrow.
```

## 最终连接细节校正

```text
Edit the supplied infographic. Preserve the ENTIRE current image, including every label, card, position, color, and corrected DGX boundary. Only repair these tiny connector details:
1. The amber horizontal connector labeled "候选 diff / 复核结果" currently stops at the green dashed DGX boundary before reaching the StepFun card. Extend it through the short gap all the way to the LEFT EDGE of the StepFun card. Put a right-pointing amber arrowhead on that card edge. Keep its other arrowhead pointing up into the patch gate, so gate and external StepFun are visibly connected in both directions.
2. The short vertical red connector from "红方自挖风险" DOWN to "授权靶场" must have a single DOWNWARD arrowhead at the target top edge. Erase any tiny upward triangle/arrowhead near the red-team bottom edge.
3. The long red POC loop on the far left may remain two-way, since findings are reviewed and then reused. Add a small red label "经验沉淀 / 复用" along a free portion of that left feedback path, without overlapping any text or nodes.
No other changes. Do not move the DGX boundary or put StepFun back inside it.
```

