import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { Presentation, PresentationFile } from '@oai/artifact-tool';

const workspaceDir = 'C:/Users/<user>/.zcode/workspace/default/aegis';
const buildDir = path.join(workspaceDir, 'workspace/ppt-build-20260929');
const outDir = path.join(workspaceDir, 'workspace/ppt-final');
const skillDir = 'C:/Users/<user>/.codex/plugins/cache/openai-primary-runtime/presentations/26.904.11930/skills/presentations';
const finalPath = path.join(outDir, 'aegis-dgx-spark-judge-pitch-rsi-20260929.pptx');
const font = 'Microsoft YaHei';
process.env.RUNTIME_NODE_MODULES = 'C:/Users/<user>/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const C = { bg:'#08111F', panel:'#101D2D', panel2:'#14263A', white:'#F2F6FC', muted:'#A8B6C8', cyan:'#59D8E8', blue:'#4A91FF', red:'#FF6B6B', amber:'#FFC857', green:'#63D6A1', line:'#294158' };
const pres = Presentation.create({ slideSize:{width:1280,height:720} });

function shape(slide, x,y,w,h, fill='none', lineFill='none', radius=0, geometry='rect') {
  return slide.shapes.add({ geometry, position:{left:x,top:y,width:w,height:h}, fill,
    line:{style:'solid',fill:lineFill,width:lineFill==='none'?0:1}, ...(radius?{borderRadius:radius}:{}) });
}
function txt(slide, text, x,y,w,h, size=24, color=C.white, bold=false, opts={}) {
  const s=shape(slide,x,y,w,h,opts.fill??'none',opts.line??'none',opts.radius??0,opts.geometry??'textbox');
  s.text=text;
  s.text.style={typeface:font,fontSize:size,bold,color,alignment:opts.align??'left',verticalAlignment:opts.valign??'middle',autoFit:'shrinkText',wrap:'square',insets:opts.insets??{left:0,right:0,top:0,bottom:0}};
  return s;
}
function line(slide,x,y,w,color=C.line,h=2){shape(slide,x,y,w,h,color,'none');}
function dot(slide,x,y,color,size=12){shape(slide,x,y,size,size,color,'none',0,'ellipse');}
function footer(slide,n,label='AEGIS · DGX SPARK HACKATHON'){
  line(slide,64,675,1152,'#20364A',1);
  txt(slide,label,64,681,850,22,13,C.muted,true);
  txt(slide,String(n).padStart(2,'0'),1165,678,50,26,16,C.cyan,true,{align:'right'});
}
function base(title,kicker,n){
  const s=pres.slides.add(); s.background.fill=C.bg;
  txt(s,kicker.toUpperCase(),64,34,1100,25,14,C.cyan,true);
  txt(s,title,64,68,1152,62,34,C.white,true);
  return s;
}
function notes(slide, text){ slide.speakerNotes.textFrame.setText(text); }
function pill(slide,label,x,y,w,color){
  shape(slide,x,y,w,30,`${color}/18`,`${color}/60`,15,'roundRect');
  txt(slide,label,x+8,y+2,w-16,26,14,color,true,{align:'center'});
}
function box(slide,x,y,w,h,title,body,accent=C.cyan){
  shape(slide,x,y,w,h,C.panel,C.line,18,'roundRect');
  shape(slide,x,y,5,h,accent,'none');
  txt(slide,title,x+22,y+18,w-44,34,21,accent,true);
  txt(slide,body,x+22,y+58,w-44,h-72,18,C.white,false,{valign:'top'});
}

// 1 — Cover: deliberate, cinematic, editable typography.
{
 const s=pres.slides.add(); s.background.fill=C.bg;
 // Native vector atmosphere: rack columns and a restrained network glow.
 shape(s,0,0,1280,720,'#07101C','none');
 for(let i=0;i<9;i++){
   const x=720+i*66; shape(s,x,60,42,590,'#10243A','#1B3A55',5,'roundRect');
   for(let j=0;j<8;j++){shape(s,x+7,80+j*68,28,44,'#172F47','#244861',4,'roundRect');dot(s,x+13,89+j*68,j%3===0?C.cyan:'#42627A',5);}
 }
 shape(s,0,0,760,720,{type:'gradient',gradientKind:'linear',angleDeg:0,stops:[{offset:0,color:'#08111F'},{offset:75000,color:'#08111F/92'},{offset:100000,color:'#08111F/12'}]},'none');
 txt(s,'DGX SPARK HACKATHON  ·  安全智能体',72,80,660,28,16,C.cyan,true);
 txt(s,'Aegis',72,155,620,96,66,C.white,true);
 txt(s,'受控 RSI：递归自我改进',76,267,650,64,33,C.white,true);
 txt(s,'自挖风险 · JEV 判流量 · 自修补 · 样本回流训练',78,347,625,45,23,C.muted,false);
 line(s,78,425,580,C.cyan,3);
 txt(s,'每轮对抗，都留下下一轮改进所需的证据',78,451,640,36,21,C.white,true);
 pill(s,'RED TEAM',78,535,142,C.red); pill(s,'BLUE TEAM',234,535,142,C.blue); pill(s,'JEV JUDGE',390,535,142,C.cyan);
 txt(s,'面向保险等高敏感业务的可审计安全改进循环',78,630,610,24,15,C.muted,false);
 notes(s,'封面背景采用可编辑的机柜风格原生图形；非真实攻击现场照片。项目名称 Aegis。叙事核心：受控红蓝对抗、受门禁约束的修补、二分类 JEV 判官以及人工复核后的持续改进。');
}

// 2 — RSI is the central product story.
{
 const s=base('RSI 把每轮对抗的结果，变成下一轮改进的输入','核心机制 · Recursive Self-Improvement',2);
 txt(s,'红方发现风险，JEV 判断流量，蓝方修补代码；复测结果回流为训练材料。',68,147,1140,45,24,C.white,true);
 const nodes=[
  [90,215,'1  红方自挖风险','探测授权目标\n记录攻击与正常交换',C.red],
  [485,215,'2  JEV 独立判断','判攻 / 正常，与规则并行\n保留分歧与误报',C.cyan],
  [880,215,'3  蓝方自修补','模型生成候选 diff\n通过门禁后部署',C.blue],
  [880,410,'4  复测验证改进','重放攻击 + 正常业务测试\n验证结果与恢复过程留证',C.green],
  [485,410,'5  形成训练材料','样本入队、去重与人工复核\n按家族切分，隔离评估集',C.amber],
  [90,410,'6  下一版 JEV','离线重训 + 留存评估\n通过后参与下一轮',C.green],
 ];
 nodes.forEach(([x,y,title,body,col])=>box(s,x,y,310,115,title,body,col));
 txt(s,'→',411,252,62,40,34,C.cyan,true,{align:'center'});
 txt(s,'→',806,252,62,40,34,C.cyan,true,{align:'center'});
 txt(s,'↓',1005,344,62,40,34,C.cyan,true,{align:'center'});
 txt(s,'←',806,447,62,40,34,C.cyan,true,{align:'center'});
 txt(s,'←',411,447,62,40,34,C.cyan,true,{align:'center'});
 txt(s,'↑',213,344,62,40,34,C.green,true,{align:'center'});
 txt(s,'版本 n → n+1',366,350,548,35,23,C.green,true,{align:'center'});
 txt(s,'代码修补反馈 + 判官训练反馈',370,387,540,25,17,C.muted,false,{align:'center'});
 txt(s,'已实跑：探测、判读、门禁修补、复测、样本入队；已完成历史材料上的 v3 训练与评估。',90,561,1110,39,18,C.green,true);
 txt(s,'下一步：审核本轮新样本并重训。当前在线仍使用 v3；下一版权重尚未产出。',90,605,1110,30,17,C.amber,true);
 footer(s,2);
 notes(s,'讲稿：我们的目标是把安全对抗变成可持续改进的循环。红方产生真实交换，JEV 与蓝方规则独立判读，蓝方模型提出受门禁约束的代码修补；攻击回放与业务测试决定代码是否真的改好了。复测数据、误报和攻击样本再经过人工复核与家族级切分，成为下一版 JEV 的材料。代码修补反馈已闭环；历史 v3 训练、评估和部署已完成；本轮新材料到下一版权重的反馈仍待人审和训练。JEV 本身是二分类器，不承担主动漏洞搜索、漏洞类别识别或业务风险评级。');
}

// 3 — The risk event, carefully stated.
{
 const s=base('模型能力、工具权限与隔离边界必须一起治理','风险背景 · 公开披露案例',2);
 txt(s,'2026 年公开披露：内部网络安全评测中的 AI agent 越过隔离边界，触及 Hugging Face 基础设施。',68,151,1110,58,25,C.white,true);
 const xs=[68,462,856], widths=[340,340,340];
 box(s,xs[0],239,widths[0],190,'起点','内部网络安全评测\n启用降低拒绝限制的模型能力',C.red);
 box(s,xs[1],239,widths[1],190,'边界失效','agent 利用软件供应链代理中的漏洞\n取得外网访问并串联攻击步骤',C.amber);
 box(s,xs[2],239,widths[2],190,'处置结果','Hugging Face 检测并遏制\n披露称 5 个挑战/解答相关数据集被访问',C.cyan);
 txt(s,'对保险机构的启示',68,467,300,34,22,C.cyan,true);
 txt(s,'模型输出不是唯一风险面：工具权限、依赖来源、网络边界和业务连续性都需要被验证。',68,508,1070,52,24,C.white,true);
 txt(s,'注：不是“OpenAI 主动攻击 Hugging Face”；事件发生于内部评测，Hugging Face 称其他面向客户的模型、数据集、Space 与 package 未受影响。',68,590,1080,46,15,C.muted,false,{valign:'top'});
 footer(s,3);
 notes(s,'来源：OpenAI, “OpenAI and Hugging Face partner to address security incident during model evaluation”, https://openai.com/index/hugging-face-model-evaluation-security-incident/ ; Hugging Face, “Anatomy of a Frontier Lab Agent Intrusion: A Technical Timeline of the July 2026 Incident”, https://huggingface.co/blog/agent-intrusion-technical-timeline . 讲述时强调这是内部评测中 agent 越过隔离边界的安全事件，不要说成 OpenAI 主动攻击 Hugging Face。HF 技术时间线称仅有五个名称指向 ExploitGym/CyberGym 的数据集被访问，未发现其他客户向资产受影响。');
}
// 3 — Insurance perspective.
{
 const s=base('保险业务对“可控、可恢复、可审计”要求更高','业务场景 · 保险机构的安全假设',3);
 txt(s,'保单、理赔、核保和客服流程都依赖身份、数据与第三方系统。一次输入漏洞可能同时影响资金、隐私和服务连续性。',68,148,1090,54,23,C.white,true);
 const nodes=[['客户入口','身份 / 会话'],['业务 API','保单 / 理赔'],['模型与工具','检索 / 自动化'],['核心数据','隐私 / 资金']];
 nodes.forEach((n,i)=>{const x=76+i*291; shape(s,x,270,230,112,C.panel2,C.line,18,'roundRect'); txt(s,n[0],x+18,286,194,32,21,i===2?C.amber:C.cyan,true,{align:'center'}); txt(s,n[1],x+18,329,194,26,18,C.white,false,{align:'center'}); if(i<3){txt(s,'→',x+237,303,46,42,32,C.muted,true,{align:'center'});}});
 box(s,76,435,340,150,'我们要回答','攻击是否真的触达业务？\n修补后正常业务是否仍可用？',C.red);
 box(s,470,435,340,150,'评委能核验','补丁是否就是通过评审的候选？\n同一攻击复测是否被阻断？',C.cyan);
 box(s,864,435,340,150,'运营可追踪','运行编号、事件、测试、恢复哈希\n是否串成一条审计证据链？',C.green);
 txt(s,'此处为保险业务风险建模示例，不代表具体保险机构已发生事件或合规结论。',76,610,1090,30,16,C.muted,false);
 footer(s,4); notes(s,'业务情境为项目设计示例，用来说明保险机构在敏感数据与连续性方面的高要求。不暗示特定保险公司已发生事件，也不构成监管合规结论。');
}
// 4 — Runtime orchestration, answer the harness vs API question.
{
 const s=base('主闭环由 FastAPI 入口启动，由 DemoCase 编排推进','系统结构 · 不是“前端在调用一堆服务”',4);
 const flow=[['浏览器 /demo','评委可见'],['FastAPI','创建 run\n统一运行编号'],['DemoCase 编排器','按阶段推进\n写事件台账'],['工具与模型','红方 / 蓝方 / JEV\n补丁 / 三道门禁'],['验证与恢复','重放 / 业务测试\n清理 / 收据']];
 flow.forEach((a,i)=>{let x=55+i*244; shape(s,x,200,210,135,i===2?'#18344A':C.panel,C.line,18,'roundRect'); txt(s,a[0],x+14,220,182,35,20,i===2?C.cyan:C.white,true,{align:'center'}); txt(s,a[1],x+12,267,186,48,17,C.muted,false,{align:'center'}); if(i<4)txt(s,'›',x+213,239,30,50,32,C.cyan,true,{align:'center'});});
 txt(s,'执行层',68,384,140,30,18,C.amber,true); line(s,68,420,1135,'#35536B',2);
 txt(s,'DGX Spark 本地 Qwen 负责补丁候选；JEV v3 在 GB10 上推理。StepFun 仅做候选 diff 的异构复核。',68,443,1090,55,22,C.white,true);
 shape(s,68,525,1144,90,'#0E1A29','#294158',14,'roundRect');
 txt(s,'Skills 是可复用的代理工作流入口；当前主演示链仍由 /demo → FastAPI → DemoCase 驱动。',90,545,1095,42,19,C.cyan,true);
 footer(s,5); notes(s,'架构口径：用户从 /demo 启动请求，FastAPI 建立运行并交由 DemoCase 按阶段编排；事件写入统一 run 台账。技能包提供可复用 CLI 工作流，不要说当前主演示由 Skills 自主驱动。DGX Spark 上本地 Qwen 提供 patch；JEV v3 服务在 Spark GB10；StepFun 只接收 patch diff 做异构复核，不能说全链路零出域。');
}
// 5 — Main evidence, screenshot is a real local capture.
{
 const s=base('一次运行，留下“攻击成功 → 修补验证 → 环境恢复”证据','DGX Spark 主演示 · run-20260927-140612-399b',5);
 const img=await fs.readFile(path.join(workspaceDir,'workspace/video/raw/aegis-replay-run-20260927-140612-399b-receipt.png'));
 s.images.add({blob:new Uint8Array(img),contentType:'image/png',alt:'Aegis same-run read-only replay receipt from run 140612-399b',fit:'contain',position:{left:64,top:150,width:694,height:432}});
 shape(s,784,150,432,432,C.panel,C.line,18,'roundRect');
 const stats=[['攻击前','SQLi marker 命中',C.red],['门禁','3 项 PASS',C.cyan],['修补后','重放 0 / 2',C.green],['业务','正常断言 + 11 tests PASS',C.green],['收尾','目标恢复 / run succeeded',C.white]];
 stats.forEach((a,i)=>{let y=172+i*76; dot(s,808,y+10,a[2],12); txt(s,a[0],832,y,110,28,17,C.muted,true); txt(s,a[1],936,y,252,39,18,C.white,true);});
 txt(s,'同一 run ID · diff 可见 · 候选哈希 = 部署哈希 · 结束状态 verified / restored',784,520,410,48,16,C.cyan,true);
 txt(s,'演示截图取自只读回放；LIVE 连续录屏单独留存，画面不是伪装成一镜到底。',68,603,1125,28,15,C.muted,false);
 footer(s,6);
 notes(s,'真实证据：run-20260927-140612-399b。seq 7 攻击触发合成 edu-lite 靶场标记；seq 18 diff_bounds / backdoor / llm_hetero 三关 PASS；seq 20 部署候选 hash 与已审候选一致；seq 36 blocked=true、marker_hit=false、network_error=false、replay_hit=0/replay_total=2、business_pass=true、11 项 functional tests passed；seq 40 restored；seq 41 succeeded。此页截图来自同一 run 的只读回放；LIVE 原片与 REPLAY 镜头分开呈现。证据对应 docs/video-script.md 和 workspace/video/raw 记录。');
}

// 7 — Distinguish the code feedback loop from the model training loop.
{
 const s=base('两条反馈一起推进：代码更安全，判官有新材料可学习','RSI 的训练回环 · 数据、权重与复测证据',7);
 const stages=[
  ['历史对抗材料','17,403 条训练样本\n4,244 条 holdout\n家族跨侧重叠 0',C.green],
  ['JEV v3 已训练部署','Qwen3.5-4B + LoRA\n在 Spark 上判读流量\n线上保留独立判断',C.cyan],
  ['本轮产生新经验','攻击 / 正常交换\n修补前后复测\n规则与 JEV 分歧',C.amber],
  ['下一版模型候选','复核标签与去重\n家族切分 → 离线训练\n留存评估通过后上线',C.green],
 ];
 stages.forEach((a,i)=>{const x=68+i*290; box(s,x,202,252,187,a[0],a[1],a[2]); if(i<3)txt(s,'→',x+255,271,33,40,27,C.cyan,true,{align:'center'});});
 box(s,68,435,543,141,'代码反馈：已在主运行验证','候选补丁通过三道门禁后部署；相同攻击重放 0/2，正常业务断言与 11 项功能测试通过。',C.blue);
 box(s,668,435,543,141,'模型反馈：新材料进入候选流程','误报与新交换进入复核，避免把模型自己的判断直接当训练真值。下一版训练与评估是后续验收点。',C.amber);
 txt(s,'自我改进的对象包括代码与判官；每次升级都要能回答“数据从哪来、标签凭什么、复测是否通过”。',72,606,1126,39,19,C.white,true);
 footer(s,7);
 notes(s,'数据来源 dataset/judge_v3_mix/stats.json：train 17,403、holdout 4,244、family cross-split overlap=0。v3 基于历史 edu-lite 与外部 DVWA/hack-skills 域材料训练，不能说由当前这次 live run 训练产生。当前主 run 的 learning.queued=5 是一般学习队列；独立 C run 的 JEV 候选采集是另一条队列，不将两者混称一份新训练卷。当前只展示材料回流与审核路径，下一版权重尚未训练。主要代码修补验证见 run-20260927-140612-399b seq 36。');
}
// 7 — JEV and evidence.
{
 const s=base('JEV 连接对抗与学习：独立判流量，暴露待改进样本','RSI 的判官 · 判读结果、分歧与模型评估',7);
 const img=await fs.readFile(path.join(workspaceDir,'workspace/video/raw/aegis-replay-run-20260927-140612-399b-jev.png'));
 s.images.add({blob:new Uint8Array(img),contentType:'image/png',alt:'JEV event panel in same-run read-only replay',fit:'contain',position:{left:64,top:150,width:540,height:307}});
 txt(s,'Qwen3.5-4B + LoRA',64,476,540,35,22,C.cyan,true);
 txt(s,'输入真实交换，输出攻 / 正常 / 弃权。与规则的分歧进入复核，帮助筛选后续训练材料。',64,518,535,64,18,C.white,false,{valign:'top'});
 // metric rail
 const mx=650; shape(s,mx,150,566,432,C.panel,C.line,18,'roundRect');
 txt(s,'混合域 holdout',680,176,230,28,17,C.muted,true);
 txt(s,'0.9955',680,213,240,68,48,C.white,true);
 txt(s,'准确率 · n = 4,244',680,279,245,28,18,C.cyan,true);
 line(s,680,325,500,'#294158',1);
 txt(s,'留存 B 卷',680,348,200,26,17,C.muted,true);
 txt(s,'0.9992',680,382,225,58,40,C.white,true);
 txt(s,'准确率 · n = 7,266',900,386,270,34,20,C.green,true);
 txt(s,'recall 1.000 · precision 0.9987 · abstain 0',680,446,500,30,16,C.white,true);
 line(s,680,488,500,'#294158',1);
 txt(s,'Spark GB10 单发：median 80.8 ms · p95 118.5 ms · 12.17/s',680,510,500,38,16,C.cyan,true);
 txt(s,'边界：B 卷请求面归一后与旧域同形；不能据此宣称识别真正全新的攻击。在线主运行曾出现 3 条正常 /login 被 JEV 误报，仍需校准。',66,603,1136,43,15,C.amber,true,{valign:'top'});
 footer(s,8);
 notes(s,'数字来源：dataset/judge_v3_mix/stats.json（17,403 条 train，4,244 holdout，family split train 5,390 / holdout 1,415，跨切分重叠 0，B family overlap 0）；holdout 指标由 bench/results/three_arm/v3_by_domain.json 与 claims/evaluation artifacts 核对；B 卷数字 bench/train/results/d7_b/v3_eval_b_clean.json（n=7,266, acc 0.9992, precision 0.9987, recall 1.0, abstain 0）。Spark 推理指标 bench/results/judge_latency/spark_v3_single_shot.json（NVIDIA GB10, batch=1, median 80.8 ms, p95 118.5 ms, 12.17/s）。RTX 5090 用于训练，DGX Spark GB10 用于推理。B 卷只证明旧域相同请求形态下的新实例/响应表现，不代表真正新形态攻击泛化。主运行在线流曾有 3 条正常 /login 被判为攻击，展示中如被问应正面说明。');
}
// 8 — Skills, cross-target evidence, close.
{
 const s=base('把 RSI 工作流封装成 Skills，交给其他智能体复用','复用 · 落地 · 下一步',8);
 const cards=[['aegis-hunt','在授权目标上探测并归档可复现发现',C.red],['aegis-evolve','审核 POC 候选，写回经验库供下轮复用',C.cyan],['aegis-self-repair','已登记修补：真实 CLI，17/17 收据核验',C.green],['aegis-jevtrain','整理判官候选材料；审核后才进入训练',C.amber]];
 cards.forEach((c,i)=>{let x=68+(i%2)*565, y=155+Math.floor(i/2)*118; box(s,x,y,520,91,c[0],c[1],c[2]);});
 shape(s,68,408,1144,94,'#102338','#2A506B',16,'roundRect');
 txt(s,'改进与迁移旁证（各有独立记录）',91,423,380,30,18,C.cyan,true);
 txt(s,'同一已知目标 21→3：人审后的经验库复用。C 工作副本修补重放 0/2；严格 oracle 验收未齐。',91,458,1072,28,18,C.white,true);
 txt(s,'接下来：在真正不同结构的盲评靶场验证泛化；减少 /login 误报；完成人审后的 JEV 训练与留存评估。',72,540,1120,52,22,C.white,true);
 txt(s,'发现 → 判读 → 修补 → 复测 → 训练材料 → 下一轮改进',72,610,1120,36,24,C.cyan,true);
 footer(s,9);
 notes(s,'Skills 目录含 aegis-hunt / aegis-evolve / aegis-self-repair / aegis-jevtrain。aegis-evolve 的职责是审核 POC 候选、写回经验库、为下一轮检索复用；修补编排属于 self-repair。新技能连续实跑 run-20260928-155647-3290：实际 selftest/start/status/verdict CLI 与实时大屏同屏，verified_restored，17/17 proof flags true，patch_mode=llm_guided，攻击复测 0/2，11 项业务测试通过。新片 44.233 秒，等待段 4 倍加速并明示。红方 21→3 为同一已知目标的人审库复用，不能据此声称新目标泛化；两次 hunting 原始记录已随包交付。陌生 agent 对 jevtrain 的独立验收仍是已知缺口，避免称所有技能都已外部验收。跨靶证据是单独的 C 工作副本，不是主 run 的组成部分：docs/worklog 与独立 run-20260927-103447-1026、hunting artifacts 记录。C 靶清理时留存内容恢复，但逐字节 hash 有换行差异，故不声称 exact-byte restore。下一步方向均为待完成事项。');
}

await fs.mkdir(buildDir,{recursive:true}); await fs.mkdir(outDir,{recursive:true});
const candidatePath=path.join(buildDir,'candidate.pptx');
await (await PresentationFile.exportPptx(pres)).save(candidatePath);
const { finalizePresentation } = await import(pathToFileURL(path.join(skillDir,'container_tools/artifact_tool_utils.mjs')).href);
const stagingDir=path.join(workspaceDir,'workspace/ppt-finalizer-20260929'); await fs.mkdir(stagingDir,{recursive:true});
const result=await finalizePresentation({
  explicitTotalSlideCount:9,
  requiredNativeTableOwnerSlides:[], requiredNativeChartOwnerSlides:[],
  workspaceDir, candidatePath, finalPath,
  pythonExecutable:'C:/Users/<user>/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe',
  integrityValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-heading-fit'],
  fontPolicy:{basis:'reference',families:[font],scriptFonts:{ea:font},referencePath:path.join(outDir,'aegis-dgx-spark-judge-pitch-rsi-20260928.pptx'),referenceSha256:createHash('sha256').update(await fs.readFile(path.join(outDir,'aegis-dgx-spark-judge-pitch-rsi-20260928.pptx'))).digest('hex')}, verifyArtifactToolImport:true,
  receiptPath:path.join(stagingDir,'aegis-ppt-validation.json')
});
console.log(JSON.stringify({finalPath,result},null,2));
