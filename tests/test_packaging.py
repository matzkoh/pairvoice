"""wheel に同梱するファイルの列挙漏れを検出する。

pyproject の force-include は studio をファイルかディレクトリ単位で列挙している（studio/ ごとだと
node_modules やテストまで入る）。モジュールを足して列挙を忘れると、ビルドは通ったまま
wheel から黙って抜け落ちるので、ここで気づく。
"""

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "studio"


def bundled_sources() -> set[str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["force-include"])


def is_bundled(module: Path, sources: set[str]) -> bool:
    relative = module.relative_to(ROOT)
    return any(str(path) in sources for path in (relative, *relative.parents))


def test_every_studio_module_is_bundled():
    sources = bundled_sources()
    modules = [*STUDIO.glob("*.ts"), *(STUDIO / "server").rglob("*.ts")]
    missing = [
        str(module.relative_to(ROOT))
        for module in modules
        if not module.name.endswith(".test.ts") and not is_bundled(module, sources)
    ]
    assert missing == []
