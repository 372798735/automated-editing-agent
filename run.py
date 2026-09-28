"""AutoCut 启动器：免配置 PYTHONPATH，一行命令跑起来。

    python run.py doctor                     # 环境体检
    python run.py list   --source mock       # 列出项目
    python run.py run    --source mock       # 跑一个项目
    python run.py batch  --source mock       # 批量跑
    python run.py batch  --source local --path "D:/素材"
    python run.py batch  --source capcut-web # 从剪映云空间同步

等价于 `python -m autocut ...`，但不用手动设置 PYTHONPATH。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autocut.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
