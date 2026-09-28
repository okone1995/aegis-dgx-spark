#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Aegis judge v0 —— Qwen3.5-4B LoRA 微调（本机 5090，bf16 不量化）。

消费 dataset/judge_v0/train.jsonl，holdout 只做每 epoch 的 loss 监控；
产物 = LoRA adapter + tokenizer 副本 + 训练摘要 JSON（数字全部来自实跑）。

口径（与 dataset/judge_v0/README.md 训练方案对齐；两处修正如实记录在 README）：
  - 承诺：LoRA r=16 alpha=32 dropout=0.05，q/k/v/o + gate/up/down；
          lr=1e-4 cosine、3 epoch、cutoff=2048、effective batch=16；
          仅 assistant 标签 token 计损（其余 -100）。
  - 修正 1（2026-09-24 用户拍板）：训练侧 bf16 全精度加载，不做 QLoRA NF4
          （5090 32GB 装得下；量化只在 Spark 运行时侧做 GGUF Q4）。--quant nf4 保留为降级开关。
  - 修正 2：Qwen3.5 模板默认在 assistant 轮前注空 think 块，统一 enable_thinking=False
          让判读位直接落在标签 token 上（前缀对齐有 assert 兜底）。
  - 事实：AutoModelForCausalLM 对 qwen3_5 映射到纯文本塔 Qwen3_5ForCausalLM（vision 不进训练）；
          target 里 q/k/v/o 只命中 8 个 full_attention 层，24 个 linear_attention 层对应模块
          叫 in_proj_*/out_proj，按 README 承诺不挂 LoRA。
  - 数据实测：全量 train token 长度 max=487（p50=146），cutoff 2048 永不触发。

用法（Git-Bash，仓库根）:
  /c/Users/<user>/anaconda3/envs/aegis/python.exe bench/train/train_lora.py \
      [--smoke]            # 32 条样本 2 step 冒烟：验证管线，不验证收敛
"""
import argparse
import json
import os
import pathlib
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "dataset" / "judge_v0"

# 同 eval_logit：脱敏误伤过源码默认值，改 env 优先 + home 兜底
DEFAULT_MODEL = os.environ.get("AEGIS_BASE_4B") or str(pathlib.Path.home() / "models" / "Qwen3.5-4B")
DEFAULT_OUT = REPO / "bench" / "train" / "out" / "judge_v0_lora"

# README 承诺超参 —— 改这里必须同步改 README
LORA_R, LORA_ALPHA, LORA_DROPOUT = 16, 32, 0.05
TARGET_MODULES = ["q_proj", "k_proj", "v_proj", "o_proj",
                  "gate_proj", "up_proj", "down_proj"]
LR, WARMUP_RATIO, EPOCHS, CUTOFF, EFF_BATCH = 1e-4, 0.03, 3, 2048, 16
SEED = 20260924

LABELS = ("attack", "benign")


def load_jsonl(p):
    with open(p, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def encode_record(tok, rec, cutoff=CUTOFF):
    """ChatML → input_ids + labels（prompt 全 -100，只留 assistant 标签段）。"""
    msgs = rec["messages"]
    sid = rec["meta"]["sample_id"]
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] in LABELS, sid
    full = tok.apply_chat_template(msgs, tokenize=False,
                                   add_generation_prompt=False, enable_thinking=False)
    gen = tok.apply_chat_template(msgs[:-1], tokenize=False,
                                  add_generation_prompt=True, enable_thinking=False)
    fid = tok(full, add_special_tokens=False).input_ids
    gid = tok(gen, add_special_tokens=False).input_ids
    assert fid[:len(gid)] == gid, f"prefix misalign {sid}"
    assert len(fid) <= cutoff, f"overlong {sid} len={len(fid)}"
    labels = [-100] * len(gid) + fid[len(gid):]
    assert any(l != -100 for l in labels)
    return {"input_ids": fid, "attention_mask": [1] * len(fid), "labels": labels}


class JudgeDataset:
    def __init__(self, tok, recs, cutoff):
        # overlong 走 encode_record 的 assert 一并计入 bad（实测数据 max 487 token，
        # 该分支从未触发；不再单设永不递增的 too_long 键——评审 P2）
        self.feat, self.stats = [], {"bad": 0}
        for rec in recs:
            try:
                self.feat.append(encode_record(tok, rec, cutoff))
            except AssertionError:
                self.stats["bad"] += 1
        print(f"[data] built={len(self.feat)} bad={self.stats['bad']}", flush=True)

    def __len__(self):
        return len(self.feat)

    def __getitem__(self, i):
        import numpy as np
        import torch
        return {k: torch.from_numpy(np.asarray(v)) for k, v in self.feat[i].items()}


def _gate(data_dir, allow_legacy: bool = False) -> None:
    """Fail closed before importing/loading a model. Legacy is content-pinned."""
    import hashlib
    d = pathlib.Path(data_dir)
    frozen_v0 = {
        "train.jsonl": "3ec85a0f381c6fbb786f193e646f56027a78aebc82cbe1bca7664a745be74a54",
        "holdout.jsonl": "da1445c8f4cb83941df1ffdfbdf056518400e732add5a5789a56f16aa8b6d3c6"}
    if allow_legacy:
        if d.name == "judge_v0" and all((d/n).is_file() and hashlib.sha256((d/n).read_bytes()).hexdigest()==h
                                       for n,h in frozen_v0.items()):
            print("[gate] frozen judge_v0 exact-content reproduction only")
            return
        print("[gate] rejected: legacy flag is not a generic bypass")
        raise SystemExit(3)
    reasons=[]
    try:
        man=json.loads((d/"MANIFEST.json").read_text(encoding="utf-8"))
        if not isinstance(man,dict):
            raise ValueError("manifest is not an object")
    except (OSError,ValueError):
        print("[gate] rejected: missing or malformed MANIFEST")
        raise SystemExit(3)
    if man.get("schema") != "jevtrain-manifest-v1": reasons.append("schema missing/unsupported")
    if man.get("training_ready") is not True: reasons.append("training_ready must be boolean true")
    if man.get("rows_needing_review") != 0: reasons.append("unresolved labels or missing review count")
    if not isinstance(man.get("training_blockers"),list) or man["training_blockers"]:
        reasons.append("quality blockers or missing blocker list")
    breg=man.get("b_sealed_registry")
    if (not isinstance(breg,dict) or type(breg.get("loaded_families")) is not int
            or breg["loaded_families"] <= 0 or breg.get("sources_missing") != []
            or not breg.get("sources") or any(s.get("status") != "ok" for s in breg.get("sources",[]))):
        reasons.append("sealed registry not fully available")
    files=man.get("files")
    all_rows=[]
    counts={}
    for split in ("train.jsonl","holdout.jsonl"):
        p=d/split
        entries = files if isinstance(files,list) else []
        fe=next((f for f in entries if isinstance(f,dict) and f.get("file")==split),{})
        want=fe.get("sha256")
        if not isinstance(want,str) or len(want)!=64:
            reasons.append(split+": missing hash")
        if not p.is_file(): reasons.append(split+": missing file");continue
        if hashlib.sha256(p.read_bytes()).hexdigest()!=want: reasons.append(split+": hash mismatch")
        split_rows=[]
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                if not line.strip():continue
                row=json.loads(line);meta=row.get("meta") or {}
                if (meta.get("review_state")!="approved" or not meta.get("reviewer") or not meta.get("reviewed_at")
                        or meta.get("label_review_state") not in ("auto_ok","human_reviewed")
                        or not meta.get("queue_sample_id") or not meta.get("family")):
                    reasons.append(split+": missing approval/label provenance")
                if meta.get("label_review_state")=="human_reviewed" and not meta.get("label_audit"):
                    reasons.append(split+": missing human label audit")
                msgs=row.get("messages") or []
                if not msgs or msgs[-1].get("content") not in ("attack","benign"):
                    reasons.append(split+": invalid classifier label")
                split_rows.append(row)
        except (ValueError,TypeError,AttributeError): reasons.append(split+": malformed record")
        counts[split]=len(split_rows)
        if not split_rows or fe.get("rows")!=len(split_rows): reasons.append(split+": empty/count mismatch")
        all_rows.append(split_rows)
    if len(all_rows)==2:
        families=[{r.get("meta",{}).get("family") for r in split} for split in all_rows]
        if families[0]&families[1]: reasons.append("train/holdout family overlap")
        for split in all_rows:
            if {(r.get("messages") or [{}])[-1].get("content") for r in split}!={"attack","benign"}:
                reasons.append("both splits need both labels")
    if reasons:
        print("[gate] rejected: "+"; ".join(sorted(set(reasons))))
        raise SystemExit(3)
    print("[gate] verified manifest, hashes, labels, approvals and family separation")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--data", default=str(DATA),
                    help="训练卷目录（含 train.jsonl/holdout.jsonl）。默认 dataset/judge_v0 "
                         "以保持 v1 复现；v2 必须显式传 --data dataset/judge_v2_dvwa")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--quant", choices=["none", "nf4"], default="none",
                    help="none=bf16 全量（5090 32GB 路线，默认）；nf4=README 原 QLoRA 口径")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--allow-frozen-legacy", action="store_true",
                    help="豁免训练门禁（仅限已冻结历史卷复现）；默认必须过门禁")
    ap.add_argument("--batch-size", type=int, default=4,
                    help="默认 4（v2 起）：v1 用 8 时峰值 30.8 GiB 顶到 5090 32GB 上限、"
                         "偶发交换拖慢；grad_accum 按 EFF_BATCH 自动补到 4，"
                         "effective batch 仍是承诺的 16，优化行为不变")
    ap.add_argument("--grad-accum", type=int, default=None,
                    help="默认按 EFF_BATCH/batch_size 联动，保证 effective batch=16 承诺")
    ap.add_argument("--lr", type=float, default=LR)
    ap.add_argument("--cutoff", type=int, default=CUTOFF)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--note", default="", help="写进 summary 的备注")
    args = ap.parse_args()
    _gate(args.data, allow_legacy=bool(getattr(args, "allow_frozen_legacy", False)))
    if args.smoke:                                    # 冒烟永不写正式产物目录（评审 P1-1）
        args.out = str(Path(args.out) / "smoke")
    if args.grad_accum is None:
        args.grad_accum = max(EFF_BATCH // args.batch_size, 1)
    assert args.batch_size * args.grad_accum == EFF_BATCH or args.smoke, \
        f"effective batch {args.batch_size * args.grad_accum} != 承诺 {EFF_BATCH}"

    t0 = time.time()
    import torch
    torch.manual_seed(SEED)
    from transformers import (AutoModelForCausalLM, AutoTokenizer, Trainer,
                              TrainingArguments, DataCollatorForSeq2Seq,
                              TrainerCallback)
    from peft import LoraConfig, get_peft_model

    tok = AutoTokenizer.from_pretrained(args.model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    model_kw = dict(dtype=torch.bfloat16, device_map={"": 0})
    model = None
    if args.quant == "nf4":
        from transformers import BitsAndBytesConfig
        from peft import prepare_model_for_kbit_training
        model_kw["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16, bnb_4bit_use_double_quant=True)
    model = AutoModelForCausalLM.from_pretrained(args.model, **model_kw)
    if args.quant == "nf4":
        model = prepare_model_for_kbit_training(model)
    model.config.use_cache = False
    model.gradient_checkpointing_enable()
    # v5 下 Trainer 会带 use_reentrant=False 重开并在 PEFT/nf4 路径自动 input-grads（评审 P2-5），
    # 此处不再手工 enable_input_require_grads。

    lora = LoraConfig(r=LORA_R, lora_alpha=LORA_ALPHA, lora_dropout=LORA_DROPOUT,
                      target_modules=TARGET_MODULES, bias="none",
                      task_type="CAUSAL_LM")
    model = get_peft_model(model, lora)
    trainable, total = model.get_nb_trainable_parameters()
    print(f"[lora] trainable {trainable/1e6:.2f}M / {total/1e6:.1f}M "
          f"({trainable/total:.3%})", flush=True)

    train_recs = load_jsonl(pathlib.Path(args.data) / "train.jsonl")
    hold_recs = load_jsonl(pathlib.Path(args.data) / "holdout.jsonl")
    if not args.smoke and args.data != str(DATA):
        # v2 起数据目录可变：漏传 --data 就会静默重训 v1 那张卷，产物还长得一样
        print(f"[data] {args.data}", flush=True)
    if args.smoke:
        train_recs, hold_recs = train_recs[:32], hold_recs[:16]
    ds_tr = JudgeDataset(tok, train_recs, args.cutoff)
    ds_ho = JudgeDataset(tok, hold_recs, args.cutoff)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log_path = out / "train_log.jsonl"
    eval_path = out / "eval_loss.jsonl"
    for p in (log_path, eval_path):               # 每次 run 从干净日志开始（评审 P1-1/P2 轮换补全）
        if p.exists():
            p.rename(p.with_name(f"{p.stem}.{time.strftime('%Y%m%d-%H%M%S')}.old{p.suffix}"))

    class JsonlLog(TrainerCallback):
        def on_log(self, a, st, control, logs=None, **kw):
            if logs:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"step": st.global_step,
                                        "epoch": round(st.epoch or 0, 3),
                                        **{k: (round(v, 6) if isinstance(v, float) else v)
                                           for k, v in logs.items()}},
                                       ensure_ascii=False) + "\n")

        def on_evaluate(self, a, st, control, metrics=None, **kw):   # 评审 P1-3
            if metrics:
                with open(eval_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"step": st.global_step,
                                        "epoch": round(st.epoch or 0, 3),
                                        **{k: (round(v, 6) if isinstance(v, float) else v)
                                           for k, v in metrics.items()
                                           if not k.startswith("predict_")}},
                                       ensure_ascii=False) + "\n")

    ta = TrainingArguments(
        output_dir=args.out,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_steps=WARMUP_RATIO,       # transformers 5.x: warmup_ratio 移除，<1 按占比解释
        weight_decay=0.0,
        bf16=True,
        optim="adamw_torch",
        gradient_checkpointing=True,
        logging_steps=10,
        save_strategy="epoch" if not args.smoke else "no",
        eval_strategy="epoch" if not args.smoke else "no",
        seed=SEED,
        report_to=[],
        remove_unused_columns=False,
        dataloader_num_workers=0,          # Windows 多进程加载器易出幺蛾子
    )
    if args.smoke:
        ta.max_steps = 2

    coll = DataCollatorForSeq2Seq(tok, padding=True, max_length=args.cutoff,
                                  pad_to_multiple_of=8, label_pad_token_id=-100)
    tr = Trainer(model=model, args=ta, train_dataset=ds_tr, eval_dataset=ds_ho,
                 data_collator=coll, processing_class=tok, callbacks=[JsonlLog()])
    result = tr.train()
    tr.save_model(str(out / "adapter"))
    tok.save_pretrained(str(out / "adapter"))

    summary = {
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": str(args.model), "quant": args.quant, "smoke": args.smoke,
        # 训练卷必须自证身份：v1/v2 两版权重同格式落盘，只有这条能区分它是吃哪张卷长出来的
        "data_dir": str(args.data),
        "hyperparams": {"lora_r": LORA_R, "alpha": LORA_ALPHA, "dropout": LORA_DROPOUT,
                        "targets": TARGET_MODULES, "lr": args.lr, "epochs": args.epochs,
                        "cutoff": args.cutoff, "batch": args.batch_size,
                        "grad_accum": args.grad_accum, "seed": SEED},
        "trainable": {"n": trainable, "total": total, "ratio": round(trainable / total, 6)},
        "data": {"train": len(ds_tr), "holdout": len(ds_ho),
                 "train_stats": ds_tr.stats, "holdout_stats": ds_ho.stats},
        "train_metrics": {k: (round(v, 6) if isinstance(v, float) else v)
                          for k, v in result.metrics.items()},
        # 评审P1-2 返工：README 引用的"峰值显存"必须有 JSON 一手出处（torch 实测口径，
        # reserved 高水位；此前 23GiB 系控制台目测无源，已从文档删除/改引本字段）
        "peak_vram_gib": (round(torch.cuda.max_memory_reserved() / 2**30, 1)
                          if torch.cuda.is_available() else None),
        "wall_seconds": round(time.time() - t0, 1),
        "note": args.note,
    }
    with open(out / "train_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print("[done]", json.dumps(summary["train_metrics"], ensure_ascii=False),
          f"wall={summary['wall_seconds']}s", flush=True)


if __name__ == "__main__":
    main()
