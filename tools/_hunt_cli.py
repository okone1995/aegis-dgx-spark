"""hunt 的 CLI 驱动（供脚本调用，避免依赖 `python -c` 的引号行为）。"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.hunt import main  # noqa: E402

sys.exit(main(sys.argv[1:]))
