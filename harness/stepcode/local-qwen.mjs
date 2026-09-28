/**
 * Aegis local-qwen provider — Step Code 扩展
 * 将本地 SGLang (Qwen3.8-Flash-Next) 注册为 OpenAI 兼容 provider（D2 spike #①）。
 * 用法: step -e harness/stepcode/local-qwen.mjs --provider local-qwen --model qwen38-flash ...
 */
export default function (pi) {
  pi.registerProvider("local-qwen", {
    name: "Aegis Local Qwen (SGLang)",
    baseUrl: process.env.AEGIS_SGLANG_URL || "http://127.0.0.1:30000/v1",
    apiKey: process.env.AEGIS_KEY,  // 密钥只走 env（D6 P1：旧默认 key 已作废轮换）
    api: "openai-completions",
    models: [
      {
        id: "qwen38-flash",
        name: "Qwen3.8-Flash-Next NVFP4 (local)",
        reasoning: true,
        input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
        contextWindow: 262144,
        maxTokens: 8192,
      },
    ],
  });
}
