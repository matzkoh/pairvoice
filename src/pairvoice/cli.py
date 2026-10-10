"""pairvoice の CLI。サーバーを起動するか、HTTP で叩くだけの薄いラッパ。"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from urllib import error

from . import client, launchd, logs
from .config import DEFAULT_CONFIG_PATH, load_config

# terminate を無視する子を待つ上限。メニューバーは重い後始末を持たないので短くてよい。
MENUBAR_STOP_TIMEOUT = 5
# warmup は読み込み終わるまで待つ。初回はモデルのダウンロード（約 15GB）を含む
WARMUP_TIMEOUT_SECONDS = 3600
_DURATION = re.compile(r"^(\d+)(m|h)?$")
MIN_MINUTES = 1
MAX_MINUTES = 480


def parse_duration(text: str) -> int:
    match = _DURATION.match(text.strip())
    if not match:
        raise ValueError(f"解釈できない期間: {text!r}（例: 30m, 1h, 45）")
    value, unit = int(match.group(1)), match.group(2)
    minutes = value * 60 if unit == "h" else value
    if not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError(f"{MIN_MINUTES}〜{MAX_MINUTES} 分の範囲で指定してください")
    return minutes


def _menubar_command(config_path: str | None, log_file: Path | None = None) -> list[str]:
    """メニューバーを子として起こすコマンド。

    `uv run` を経由せず同じ venv の python を直に使う。`uv tool install` で
    入れた環境には `uv run` が拾うプロジェクトが無い。
    """
    config = ["--config", config_path] if config_path else []
    return [
        sys.executable,
        "-m",
        "pairvoice",
        *config,
        "menubar",
        "--parent-pid",
        str(os.getpid()),
        *(["--log-file", str(logs.menubar_log_path(log_file))] if log_file else []),
    ]


def _stop_menubar(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=MENUBAR_STOP_TIMEOUT)
    except subprocess.TimeoutExpired:
        child.kill()


@contextlib.contextmanager
def _menubar_child(config_path: str | None, log_file: Path | None = None):
    """serve と同じ寿命でメニューバーを抱える。

    起こせなくても serve は続ける（読み上げが本体で、アイコンは付属物である）。
    親が SIGKILL された場合は finally も signal ハンドラも走らないが、子が親の不在を
    見て自分で終わる。
    """
    try:
        child = subprocess.Popen(_menubar_command(config_path, log_file))
    except OSError as failed:
        print(f"メニューバーを起こせませんでした（続行します）: {failed}", file=sys.stderr)
        child = None

    def stop_and_exit(signum, frame):
        """SIGTERM / SIGINT を受けた最後に呼ばれ、子を片付けて 0 で降りる。

        uvicorn は捕まえた signal を、元のハンドラを戻したうえで撃ち直す
        （`uvicorn/server.py` の `capture_signals`）。既定のハンドラのままだと
        graceful shutdown の直後に signal 死するので、この with の finally は走らず、
        終了ステータスも 143 になる。LaunchAgent の `KeepAlive`
        （`SuccessfulExit: false`）はそれをクラッシュと見て復活させてしまい、
        メニューの「pairvoice を終了」が10秒後に元へ戻る。
        """
        if child is not None:
            _stop_menubar(child)
        os._exit(0)

    previous = {sig: signal.signal(sig, stop_and_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        yield child
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        if child is not None:
            _stop_menubar(child)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pairvoice")
    parser.add_argument("--config", default=None, help="設定ファイルのパス")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="サーバーを起動する")
    serve_parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="ログを書くファイル（回しながら書く）。省くと端末に出す",
    )
    sub.add_parser("warmup", help="モデルを読み込み、終わるまで待つ（初回はダウンロードも）")
    sub.add_parser("unmute", help="ミュートを解除する")
    sub.add_parser("status", help="状態を表示する")
    sub.add_parser("restart", help="常駐サーバーを再起動する（設定の反映に必要）")
    sub.add_parser("install", help="常駐サーバーを登録し、データの置き場所に雛形を置く")
    sub.add_parser("uninstall", help="常駐サーバーの登録を外す（データは残す）")
    menubar_parser = sub.add_parser("menubar", help="メニューバーに常駐する")
    menubar_parser.add_argument(
        "--parent-pid",
        type=int,
        default=None,
        help="親（serve）の PID。消えたら一緒に終了する",
    )
    menubar_parser.add_argument(
        "--log-file", type=Path, default=None, help="ログを書くファイル。省くと stderr に出す"
    )

    sub.add_parser("studio", help="studio（常駐サーバーが配る画面）をブラウザで開く")

    mute = sub.add_parser("mute", help="期限付きでミュートする")
    mute.add_argument("duration", help="30m / 1h / 45（分）")

    say = sub.add_parser("say", help="任意のテキストを読み上げる（ミュートをバイパスする）")
    say.add_argument("text")
    say.add_argument("--voice", help="声（プロファイル）の名前か ID。省くと使用中の声")
    say.add_argument(
        "--style",
        help="スタイル（caption とサンプラーの組）の名前。studio の「スタイル」画面で作る",
    )
    say.add_argument(
        "--caption", help="話し方の指示。スタイルやプロファイルの caption より優先する"
    )

    sub.add_parser("stop", help="鳴っている読み上げと、待っている読み上げを止める")

    evaluate = sub.add_parser("eval", help="要約プロンプト・要約モデルを規則で評価する")
    evaluate.add_argument(
        "--prompt", type=Path, default=None, help="評価するプロンプト（既定は使用中の prompt.txt）"
    )
    evaluate.add_argument(
        "--cases",
        type=Path,
        default=None,
        help="ケースの TSV（id<TAB>input、1行目はヘッダ）。既定は同梱のケース",
    )
    evaluate.add_argument(
        "--reviews", action="store_true", help="studio のレビュー（👍 / 👎）もケースに加える"
    )
    evaluate.add_argument(
        "--model",
        default=None,
        help="このプロセスに読み込んで評価するモデル。省くと常駐サーバーのモデルで要約する",
    )
    evaluate.add_argument("--out", type=Path, default=None, help="ケースごとの結果を書く JSONL")

    args = parser.parse_args(argv)

    # 設定を読まないコマンドは、壊れた config.toml でも動かす（直す前に外したい・止めたい）
    if args.command == "restart":
        return launchd.restart()

    if args.command == "uninstall":
        from . import install

        return install.uninstall()

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as broken:
        print(
            f"設定ファイルを読めません（{args.config or DEFAULT_CONFIG_PATH}）: {broken}",
            file=sys.stderr,
        )
        return 2
    base = f"http://127.0.0.1:{config.port}"

    if args.command == "serve":
        return _serve(config, args.config, log_file=args.log_file)

    if args.command == "install":
        from . import install

        # 常駐サーバーにも同じ設定を読ませる。launchd は / で起こすので絶対パスにする。
        # リンクは辿らない（load_config と同じく absolute()）。相対パスの基準は設定ファイルの
        # ディレクトリなので、辿るとシェルと launchd で基準がずれる
        config_path = Path(args.config).expanduser().absolute() if args.config else None
        return install.install(
            config, data_root=install.default_data_root(), config_path=config_path
        )

    if args.command == "menubar":
        from . import menubar

        if args.log_file is not None:
            logs.configure_file(args.log_file)
        return menubar.run(base, parent_pid=args.parent_pid)

    if args.command == "eval":
        return _eval(config, base, args)

    if args.command == "studio":
        return _studio(base)

    try:
        if args.command == "mute":
            minutes = parse_duration(args.duration)
            result = client.call(base, "/mute", body={"minutes": minutes})
        elif args.command == "unmute":
            result = client.call(base, "/unmute")
        elif args.command == "warmup":
            print(
                "モデルを読み込んでいます（初回はダウンロードも含めて時間がかかる）…",
                file=sys.stderr,
            )
            result = client.call(base, "/warmup", timeout=WARMUP_TIMEOUT_SECONDS)
        elif args.command == "status":
            result = client.call(base, "/health", method="GET")
        elif args.command == "say":
            body = {"text": args.text, "bypass_mute": True}
            for key in ("voice", "style", "caption"):
                if getattr(args, key) is not None:
                    body[key] = getattr(args, key)
            result = client.call(base, "/speak", body=body)
        elif args.command == "stop":
            result = client.call(base, "/stop")
        else:  # argparse が防ぐので到達しない
            raise AssertionError(args.command)
    except ValueError as invalid:
        print(str(invalid), file=sys.stderr)
        return 2
    except error.HTTPError as failed:
        print(_http_error_message(failed), file=sys.stderr)
        return 1
    except (OSError, error.URLError) as unreachable:
        print(
            f"pairvoice に接続できません（{base}）: {unreachable}",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _http_error_message(failed: error.HTTPError) -> str:
    detail = failed.read().decode(errors="replace")
    return f"pairvoice がエラーを返しました（HTTP {failed.code}）: {detail}"


def _eval(config, base: str, args: argparse.Namespace) -> int:
    from . import evaluation
    from .config import default_data_root
    from .prompt import PROMPT_FILENAME

    data_root = default_data_root()
    prompt_path = args.prompt or data_root / PROMPT_FILENAME
    try:
        system = prompt_path.read_text(encoding="utf-8")
        cases = evaluation.load_tsv_cases(args.cases or evaluation.DEFAULT_CASES)
    except (OSError, ValueError) as missing:
        # ValueError は UTF-8 でないファイル
        print(f"読めません: {missing}", file=sys.stderr)
        return 2
    if args.out:
        # 書けないと分かるのが全ケースを要約し終えた後だと、待った時間が無駄になる。
        # ファイルは作らない（途中で失敗したとき、空の結果が走り切った評価に見える）
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
        except OSError as unwritable:
            print(f"書けません: {unwritable}", file=sys.stderr)
            return 2
        target = args.out if args.out.exists() else args.out.parent
        if args.out.is_dir() or not os.access(target, os.W_OK):
            print(f"書けません: {args.out}", file=sys.stderr)
            return 2
    try:
        if args.model:
            # 別のモデルは常駐サーバーに載せられないので、このプロセスに読み込んで評価する
            if args.reviews:
                cases += evaluation.load_review_cases(data_root)
            summarize = evaluation.local_summarizer(args.model, config.llm.max_tokens)
            results = evaluation.run_cases(cases, summarize, system, config.eval.style)
        else:
            results = evaluation.run_on_daemon(base, system, cases, args.reviews)
    except error.HTTPError as failed:
        print(_http_error_message(failed), file=sys.stderr)
        return 1
    except (OSError, error.URLError, RuntimeError) as failed:
        print(f"要約できませんでした（{base}）: {failed}", file=sys.stderr)
        return 1

    if args.out:
        evaluation.write_results(results, args.out)
    print(evaluation.format_summary(results))
    return 0


def _studio(base: str) -> int:
    from .studio_web import open_studio

    try:
        client.call(base, "/health", method="GET", timeout=2)
    except (OSError, error.URLError):
        # 画面は常駐サーバーが配るので、止まっていると開いても何も出ない
        print(
            f"常駐サーバーに繋がりません（{base}）。`pairvoice restart` で起動してください",
            file=sys.stderr,
        )
        return 1
    open_studio(base)
    return 0


def _serve(config, config_path: str | None = None, log_file: Path | None = None) -> int:
    import socket

    import uvicorn

    from .server import build_engine, create_app

    config.tts.output_dir.mkdir(parents=True, exist_ok=True)

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", config.port))
    except OSError:
        print(
            f"port {config.port} is already in use — "
            "常駐サーバーが動いていないか確認してください（多重起動）",
            file=sys.stderr,
        )
        return 1
    finally:
        probe.close()

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)

    app = create_app(build_engine(config))
    # メニューバーは serve の子として生き、serve が終われば一緒に消える。
    with _menubar_child(config_path, log_file):
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=config.port,
            log_level="info",
            log_config=logs.uvicorn_log_config(log_file),
        )
    return 0
