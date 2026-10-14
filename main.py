"""截图自动搜题 —— 直接运行这个文件即可启动。

    python main.py        带控制台(方便看日志)
    pythonw main.py       静默驻留托盘(推荐日常使用)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from screen_qa.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())