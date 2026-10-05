"""`python -m pairvoice` の入口。

serve がメニューバーを子として起こすときに使う。`uv run` を経由せず、
serve と同じ Python を直に使う。
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
