"""常駐サーバーのログの行き先。uvicorn の log_config として渡す。

LaunchAgent から起きるときは `serve --log-file` で、ログファイルを serve 自身が
RotatingFileHandler で持つ（launchd が開くファイルは回せない）。launchd の stdout /
stderr は logging を通らない出力（落ちたときの Traceback、ネイティブ層の出力）の
受け皿として別のファイルに残す。手で serve したときは uvicorn の既定どおり端末に出す。
"""

from __future__ import annotations

import copy
import logging
import logging.handlers
from pathlib import Path

from .config import API_PREFIX

_LOG_DIR = Path.home() / "Library" / "Logs"
LOG_PATH = _LOG_DIR / "pairvoice.log"
STDERR_LOG_PATH = _LOG_DIR / "pairvoice.stderr.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 3
_HEALTH_PATH = f"{API_PREFIX}/health"


class HealthAccessFilter(logging.Filter):
    """成功した /health のアクセスログを落とす。menubar が5秒ごとに叩くので、残すとログの大半を占める。"""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) == 5:
            _client, _method, path, _version, status = args
            return not (path == _HEALTH_PATH and isinstance(status, int) and status < 400)
        return True


def menubar_log_path(log_file: Path) -> Path:
    """serve のログの隣に置く menubar のログ。別プロセスが同じファイルを回すと世代が壊れるので分ける。"""
    return log_file.with_name(f"{log_file.stem}-menubar{log_file.suffix}")


def configure_file(log_file: Path, *, max_bytes: int = MAX_BYTES) -> None:
    """uvicorn を持たないプロセス（menubar）のログを、回しながらファイルに書く。"""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=max_bytes, backupCount=BACKUP_COUNT, encoding="utf-8"
            )
        ],
    )


def uvicorn_log_config(log_file: Path | None, *, max_bytes: int = MAX_BYTES) -> dict:
    # uvicorn を読み込むのは serve のときだけにする（ほかのサブコマンドの起動を重くしない）
    import uvicorn.config

    config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    config["filters"] = {"quiet_health": {"()": HealthAccessFilter}}
    config["loggers"]["uvicorn.access"]["filters"] = ["quiet_health"]
    if log_file is None:
        return config

    # ローテートするハンドラを1本にする（同じファイルを2本で回すと互いの世代を壊す）。
    # アクセスログも既定の書式で足りる（メッセージが「addr - "GET /path HTTP/1.1" 200」になる）
    config["formatters"]["default"].update(
        fmt="%(asctime)s %(levelprefix)s %(message)s", use_colors=False
    )
    config["handlers"] = {
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "default",
            "filename": str(log_file),
            "maxBytes": max_bytes,
            "backupCount": BACKUP_COUNT,
            "encoding": "utf-8",
        }
    }
    config["loggers"]["uvicorn"]["handlers"] = ["file"]
    config["loggers"]["uvicorn.access"]["handlers"] = ["file"]
    # pairvoice の各モジュールのログもここに集める。INFO は「鳴らさなかった・止めた」のように
    # 読み上げが来なかった理由を残すものだけなので、pairvoice に限って INFO から書く
    config["root"] = {"handlers": ["file"], "level": "WARNING"}
    config["loggers"]["pairvoice"] = {"level": "INFO"}
    return config
