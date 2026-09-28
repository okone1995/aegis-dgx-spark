/* T10-S2/S3 运行态断言:逐流三列并排 + 选中流 + 分歧保留
 *
 * 目的:把"三列表与分歧计数"钉成可回归的断言 —— 一旦 _fmap(真值映射) 或
 * 选中流逻辑退化,这里会先红,不必每次都开真浏览器。
 * 注意:这是桩 DOM + 桩 fetch 的**运行态替身**(真执行前端函数体),不是浏览器渲染。
 */
"use strict";
const path = require("path");
const { createEnv } = require("./dom_env.js");

const ROOT = path.resolve(__dirname, "..", "..");

/* 两条流:一条"规则沉默但判官判攻击"的分歧流,一条"规则命中且判官判攻击"的一致流。
   真值全走事件明文 intent_label(与真机同源字段),不喂给判官。 */
const EVENTS = [
  { seq: 1, type: "run.created", stage: "preflight", flow_id: "", finding_id: "", summary: "", payload: {} },
  { seq: 2, type: "detection.completed", stage: "classify", flow_id: "FL-A", finding_id: "", summary: "",
    payload: { flow_id: "FL-A", intent_label: "benign", matched: false, class: null, endpoint: "/login", severity: null, alerts: [] } },
  { seq: 3, type: "judge.completed", stage: "classify", flow_id: "FL-A", finding_id: "", summary: "",
    payload: { flow_id: "FL-A", path: "/login", p_attack: 0.9996, verdict: "attack", band: "confident",
               raw_verdict: "attack", latency_ms: 87.5, model_version: "judge_v3_adapter", protocol_id: "judge-protocol-v1" } },
  { seq: 4, type: "detection.completed", stage: "attack", flow_id: "FL-B", finding_id: "F-001", summary: "",
    payload: { flow_id: "FL-B", intent_label: "attack", matched: true, class: "sqli", endpoint: "/news/search", severity: "high", alerts: [1] } },
  { seq: 5, type: "judge.completed", stage: "attack", flow_id: "FL-B", finding_id: "F-001", summary: "",
    payload: { flow_id: "FL-B", path: "/news/search", p_attack: 1.0, verdict: "attack", band: "confident",
               raw_verdict: "attack", latency_ms: 96.1, model_version: "judge_v3_adapter", protocol_id: "judge-protocol-v1" } },
];

const RECEIPT = {
  schema_version: 1, run_id: "run-T", case_id: "sqli", finding_id: "F-001",
  state: { value: "succeeded", source: "run.json:state" },
  pre_patch_attack: { flow_id: "FL-B", seq: 4, replay_of: "", source: "seq:4" },
  post_patch_replays: [{ flow_id: "FL-C", seq: 9, replay_of: "FL-B", source: "seq:9" }],
  deploy_seq: 6,
  business_before_patch: { value: true, source: "seq:2" },
  business_after_patch: { value: true, source: "seq:8" },
  patch_candidate: { sha256: "ac34f26f45e2", orig_sha256: "4b7c0d2d2e9a", diff_len: 1135, mode: "llm_guided",
                     model: "qwen", source: "seq:5" },
  deployed_hash: { value: "ac34f26f45e2", source: "seq:6" },
  gates: { value: { diff_bounds: "PASS", backdoor: "PASS", llm_hetero: "PASS" }, pass: true, reasons: [], source: "seq:7" },
  verification: { blocked: { value: true, source: "artifact:verification.json" },
                  replay_hit: { value: 0, source: "artifact:verification.json" },
                  replay_total: { value: 2, source: "artifact:verification.json" },
                  business_pass: { value: true, source: "artifact:verification.json" } },
  cleanup: { value: "restored", source: "seq:10" },
  generated_at: "2026-09-27T00:00:00Z",
};

let pass = 0;
const fails = [];
function chk(name, cond, got) {
  if (cond) { pass++; process.stdout.write("PASS  " + name + "\n"); }
  else { fails.push(name); process.stdout.write("FAIL  " + name + "  ::  " + String(got) + "\n"); }
}

async function main() {
  const cfg = {
    router: (url) => {
      const u = String(url);
      if (u.includes("/artifacts/evidence-receipt")) return { status: 200, body: RECEIPT };
      if (u.includes("/artifacts/probe-lineage")) return { status: 200, body: {
        enabled: true, model_calls: 1, any_hit: true, elapsed_s: 3.8, budget_s: 25, plan_error: null,
        variants: [
          { idx: 0, parent: "01_union_exfil.txt", transform_id: "inline_comment", source: "llm",
            policy: "allowed", fired: true, outcome: "exploited", flow_id: "FL-010", replay_of: "FL-003",
            markers_hit: ["M"], rationale: "关键字间插注释" },
          { idx: 1, parent: "02_error_based.txt", transform_id: "kw_case", source: "fallback",
            policy: "rejected: PolicyViolation: off-cage", fired: false, outcome: null, flow_id: null,
            replay_of: "FL-003", markers_hit: [], rationale: "大小写混淆" },
        ],
      } };
      if (/\/api\/demo\/runs\/[^/]+$/.test(u.split("?")[0])) {
        return { status: 200, body: { run: { run_id: "run-T", state: "succeeded", cleanup: "restored",
                                            repair_outcome: "verified", judge_status: "ok", learning_status: "queued" } } };
      }
      if (u.includes("/events")) {
        const after = Number((u.match(/after=(\d+)/) || [])[1] || 0);
        const page = EVENTS.filter((e) => e.seq > after);
        return { status: 200, body: { events: page, next_seq: page.length ? page[page.length - 1].seq : after,
                                     has_more: false, state: "succeeded" } };
      }
      if (u.endsWith("/api/demo/runs")) return { status: 200, body: { runs: [{ run_id: "run-T", state: "succeeded",
        created_at: "2026-09-27T00:00:00Z", judge_status: "ok", cleanup: "restored" }] } };
      if (u.includes("/api/demo/models")) return { status: 200, body: {
        models: [],
        metrics_three_layers: {
          layers: {
            offline_same_distribution: { label: "② 同分布离线", n: 4244, acc: 0.9955, precision: 0.9949,
              recall: 0.9982, tp: 2722, fp: 14, fn: 5, tn: 1503, abstain_ratio: 0.0007, band: [0.2, 0.8],
              source: "bench/results/three_arm/v3_mixed.json", unavailable: false },
            external_distribution: { label: "③ 真外部分布", status: "未采集",
              why: "尚无真外部同分布卷读数;留存 B 卷请求面按改名表归一后与训练面同形", source: null },
          },
          small_sample_weak: { items: [{ class: "csrf", n: 7, acc: 0.4286 },
            { class: "authbypass", n: 12, acc: 0.8333 }, { class: "nearmiss", n: 6, acc: 0.6667 }],
            source: "bench/results/three_arm/v3_mixed.json" },
          dataset: { train_n: 17403, holdout_n: 4244, families: { train: 5390, holdout: 1415, cross_split_overlap: 0 },
            b_family_overlap: 0, b_cores_known: 2328, source: "dataset/judge_v3_mix/stats.json" },
        },
      } };
      return { status: 200, body: {} };
    },
  };
  const env = createEnv({ root: ROOT, router: cfg.router });
  await env.settle();
  const T = (id) => String((env.byId[id] && env.byId[id].textContent) || "").replace(/\s+/g, " ").trim();

  await env.evalIn("pickRun('run-T')");
  await env.settle();
  /* 把回放推到底(页面自己的 _rpTick 链) */
  for (let i = 0; i < 40; i++) {
    await env.tickAll("timeout");
    await env.settle();
    if (String(env.evalIn("(_rp && _rp.i >= _rp.items.length) ? 'done' : 'go'")) === "done") break;
  }
  await env.settle();

  chk("三列表已渲染 flow 列", /FL-A/.test(T("j-table")) && /FL-B/.test(T("j-table")), T("j-table").slice(0, 120));
  chk("三列表含真值列取值 benign/attack", /benign/.test(T("j-table")) && /attack/.test(T("j-table")), T("j-table").slice(0, 160));
  chk("分歧流被标红(FL-A:真值 benign 而判官 attack)", /FL-A/.test(T("j-table")) && /不一致 1 条/.test(T("j-table")), T("j-table").slice(-160));
  chk("与真值不一致计数 = 1", T("j-diff") === "1", T("j-diff"));
  chk("选中流 = finding 对应的攻击流 FL-B(不是最后一条 FL-A)", T("j-sel") === "FL-B", T("j-sel"));
  chk("表盘显示选中流的 p_attack", /100%/.test(T("jv-p")), T("jv-p"));
  chk("四步收据①含补前攻击流", /FL-B/.test(T("ev-1")), T("ev-1"));
  chk("四步收据③门禁三项 PASS", /diff_bounds=PASS/.test(T("ev-3")) && /backdoor=PASS/.test(T("ev-3")), T("ev-3"));
  chk("四步收据④候选与部署 hash 一致 = yes", /候选≡部署 yes/.test(T("ev-4")), T("ev-4"));
  chk("回放推进期间无写请求", env.writes().length === 0, JSON.stringify(env.writes()));

  chk("三层指标②同分布离线已渲染且带口径", /4244/.test(T("m-l2")) && /0\.9955/.test(T("m-l2")) && /0\.2,0\.8/.test(T("m-l2")), T("m-l2").slice(0, 140));
  chk("三层指标③真外部分布如实未采集", /未采集/.test(T("m-l3")) && /不声称泛化/.test(T("m-l3")), T("m-l3").slice(0, 120));
  chk("小样本弱项如实上屏(csrf/authbypass/nearmiss)", /csrf n=7 acc=0\.4286/.test(T("m-weak")) && /nearmiss n=6/.test(T("m-weak")), T("m-weak").slice(0, 160));
  chk("三层口径并列且①带线上带 0.40-0.60", /本轮逐流/.test(T("m-l1")) && /0\.40-0\.60/.test(T("m-l1")), T("m-l1").slice(0, 140));

  chk("受限二次探针 lineage 已上屏(选招/变换/来源/命中)",
      /inline_comment/.test(T("probe-line")) && /source|llm/.test(T("probe-line")) && /命中/.test(T("probe-line")),
      T("probe-line").slice(0, 200));
  chk("探针未发射的变体如实标注(策略拒绝)", /未发射/.test(T("probe-line")) && /rejected/.test(T("probe-line")),
      T("probe-line").slice(0, 240));
  chk("命中时明写“转 regressed”且不声称所有攻击失效",
      /regressed/.test(T("probe-line")) && /不等于所有攻击失效/.test(T("probe-line")), T("probe-line").slice(-120));

  process.stdout.write("\n结果: " + pass + " passed, " + fails.length + " failed\n");
  if (fails.length) { process.stdout.write("失败项: " + fails.join(" | ") + "\n"); process.exitCode = 1; }
}
main().catch((e) => { process.stdout.write("HARNESS ERROR: " + (e && e.stack || e) + "\n"); process.exitCode = 1; });
