"""`pairvoice install` / `uninstall`。常駐の登録とデータの置き場所の初期化。

wheel から入れても clone から入れても同じ手順で済むよう、LaunchAgent は
「いま動いている Python で -m pairvoice serve」を登録する。雛形はパッケージに
同梱したもの（bundle.BUNDLE_ROOT / examples）を使う。
"""

from __future__ import annotations

import os
import plistlib
import shutil
import sys
import time
from pathlib import Path

from . import launchd, logs
from .bundle import BUNDLE_ROOT
from .config import Config, default_data_root

EXAMPLES_DIR = BUNDLE_ROOT / "examples"
DEFAULT_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
# 雛形 → データの置き場所での名前。フックと studio が読む
TEMPLATES = (("prompt.example.txt", "prompt.txt"), ("dict.example.tsv", "dict.tsv"))


def seed_data_root(data_root: Path, examples_dir: Path) -> list[Path]:
    """データの置き場所を作り、無いものだけ雛形を置く。置いたファイルを返す。

    すでにあるものは触らない（育てたプロンプトと辞書を雛形で潰さない）。
    """
    data_root.mkdir(parents=True, exist_ok=True)
    created = []
    for template, name in TEMPLATES:
        target = data_root / name
        if target.exists():
            continue
        shutil.copyfile(examples_dir / template, target)
        created.append(target)
    return created


def data_root_mismatch(config: Config, data_root: Path) -> str | None:
    """常駐サーバーとフック・studio がデータの置き場所を別々に見ていれば、その説明を返す。

    サーバーは環境変数を読まず、生成音声のパスを tts.output_dir の親を基準に返す。
    片方だけ動かすと、studio の再生が黙って失敗する。
    """
    served = config.tts.output_dir.parent
    if served.resolve() == data_root.resolve():
        return None
    return (
        f"データの置き場所（{data_root}）と config.toml の tts.output_dir の親（{served}）が"
        "ずれています。片方だけだと読み上げが鳴らないので、tts.output_dir を"
        f" {data_root / 'generations'} に揃えてください"
    )


def launch_agent(
    *,
    python: str,
    path_env: str,
    log_path: Path,
    stderr_path: Path,
    data_root_env: str | None = None,
    config_path: Path | None = None,
) -> dict:
    # launchd から起きるサーバーには利用者のシェルの環境変数が届かないので、
    # インストール時の値を焼き込む。PATH はメニューから studio を起動するとき node を
    # 探すため（launchd の既定の PATH には Homebrew も mise も載っていない）
    environment = {"PATH": path_env}
    if data_root_env:
        environment["PAIRVOICE_DATA_ROOT"] = data_root_env
    # --config はトップレベルの引数なので、サブコマンドより前に置く
    config_args = ["--config", str(config_path)] if config_path is not None else []
    return {
        "Label": launchd.LABEL,
        # ログは serve が回しながら書く。launchd に渡すのは logging を通らない出力だけ
        "ProgramArguments": [
            python,
            "-m",
            "pairvoice",
            *config_args,
            "serve",
            "--log-file",
            str(log_path),
        ],
        "EnvironmentVariables": environment,
        "RunAtLoad": True,
        # メニューの「pairvoice を終了」（終了コード 0）で落としたら復活させない。
        # クラッシュしたときだけ戻す
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "StandardOutPath": str(stderr_path),
        "StandardErrorPath": str(stderr_path),
    }


def _bootout(wait_seconds: float = 10.0) -> None:
    """登録を外し、ジョブが本当に消えるまで待つ。

    bootout はジョブが消える前に返るので、すぐ bootstrap すると「Bootstrap failed: 5」に
    なる。登録されていなければ bootout は失敗するが、それは「外れている」ので気にしない。
    """
    launchd.launchctl("bootout", launchd.target())
    deadline = time.monotonic() + wait_seconds
    while launchd.launchctl("print", launchd.target()).returncode == 0:
        if time.monotonic() >= deadline:
            print(
                f"警告: {launchd.target()} が {wait_seconds:g} 秒で外れませんでした",
                file=sys.stderr,
            )
            return
        time.sleep(0.2)


def install(
    config: Config,
    *,
    data_root: Path,
    examples_dir: Path = EXAMPLES_DIR,
    agents_dir: Path = DEFAULT_AGENTS_DIR,
    log_path: Path = logs.LOG_PATH,
    stderr_path: Path = logs.STDERR_LOG_PATH,
    config_path: Path | None = None,
) -> int:
    """config_path は --config で渡されたもの。絶対パスで渡す（launchd は / で起こす）。"""
    for created in seed_data_root(data_root, examples_dir):
        print(f"雛形を置きました: {created}")

    warning = data_root_mismatch(config, data_root)
    if warning:
        print(f"警告: {warning}", file=sys.stderr)

    agents_dir.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    plist = agents_dir / f"{launchd.LABEL}.plist"
    agent = launch_agent(
        python=sys.executable,
        path_env=os.environ.get("PATH", ""),
        log_path=log_path,
        stderr_path=stderr_path,
        # 環境変数で指定されていたときだけ焼き込む。default_data_root() が絶対パスにしている
        data_root_env=str(data_root) if os.environ.get("PAIRVOICE_DATA_ROOT") else None,
        config_path=config_path,
    )
    plist.write_bytes(plistlib.dumps(agent))

    _bootout()
    loaded = launchd.launchctl("bootstrap", launchd.domain(), str(plist))
    if loaded.returncode != 0:
        print(f"LaunchAgent を登録できませんでした: {loaded.stderr.strip()}", file=sys.stderr)
        return 1

    print(
        f"常駐サーバーを登録しました: {plist}（ログ: {log_path}、落ちたときの出力: {stderr_path}）"
    )
    print("Claude Code で読み上げるには、プラグインを入れてください:")
    print("  /plugin marketplace add matzkoh/pairvoice")
    print("  /plugin install pairvoice@pairvoice")
    return 0


def uninstall(*, agents_dir: Path = DEFAULT_AGENTS_DIR) -> int:
    """常駐の登録だけを外す。データの置き場所（プロンプト・辞書・声）は残す。"""
    _bootout()
    plist = agents_dir / f"{launchd.LABEL}.plist"
    plist.unlink(missing_ok=True)
    print(f"常駐サーバーの登録を外しました（データは {default_data_root()} に残っています）")
    return 0
