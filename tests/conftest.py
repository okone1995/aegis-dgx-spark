import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试会话一律关掉闭环自动收集：demo_case 的收集钩子写的是**跟踪中的证据队列**
# （engine.jevtrain.DEFAULT_QUEUE = dataset/review_queue/jevtrain_candidates.jsonl，
# 与 --workspace 无关），实测 `pytest tests/test_demo_case.py` 一次就追加 2 条空样本。
# 队列条数是对外交付口径（122 条），不能被一次回归跑改数。
os.environ.setdefault("AEGIS_JEVTRAIN", "off")

# Unit tests must not start a background model client on import.
os.environ.setdefault("AEGIS_JUDGE_MODE", "off")
