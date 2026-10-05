"""パッケージに同梱した studio / 雛形の置き場所。DATA_ROOT とは別物。"""

from __future__ import annotations

from pathlib import Path


def bundle_root(package_dir: Path) -> Path:
    """studio/ と examples/ を持つディレクトリ。

    wheel から入れるとパッケージの中に同梱されている（pyproject の force-include）。
    リポジトリから editable で入れると __file__ は src/pairvoice/ を指したままなので、
    2つ遡ったリポジトリ直下にある。
    """
    return package_dir if (package_dir / "studio").is_dir() else package_dir.parents[1]


BUNDLE_ROOT = bundle_root(Path(__file__).resolve().parent)
