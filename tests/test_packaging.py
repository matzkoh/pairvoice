"""wheel と plugin の版をそろえる。"""

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugin_and_package_share_one_version():
    # 公開する版は1つにそろえる。プラグインの版が上がらないと Claude Code は更新に気づかない
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / "plugin/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    assert plugin["version"] == pyproject["project"]["version"]
