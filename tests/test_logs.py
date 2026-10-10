import logging
import logging.config

import pytest

from pairvoice import logs


def access(path, status):
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", path, "1.1", status),
        None,
    )


def test_health_access_filter_drops_only_successful_health():
    health_filter = logs.HealthAccessFilter()
    assert health_filter.filter(access("/api/health", 200)) is False
    # 失敗した /health と、ほかの経路は残す
    assert health_filter.filter(access("/api/health", 500)) is True
    assert health_filter.filter(access("/api/speak", 200)) is True


def test_terminal_config_keeps_uvicorn_handlers_and_quiets_health():
    config = logs.uvicorn_log_config(None)
    assert config["handlers"]["default"]["class"] == "logging.StreamHandler"
    assert config["loggers"]["uvicorn.access"]["filters"] == ["quiet_health"]
    assert "root" not in config


@pytest.fixture
def applied(tmp_path):
    """dictConfig はプロセス全体のロガーを書き換えるので、終わったらハンドラを外して閉じる。"""
    names = ("uvicorn", "uvicorn.access", "")

    def apply(**kwargs):
        logging.config.dictConfig(logs.uvicorn_log_config(tmp_path / "pairvoice.log", **kwargs))

    yield apply
    for name in names:
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
        logger.filters.clear()
    logging.getLogger("pairvoice").setLevel(logging.NOTSET)


def test_file_config_writes_uvicorn_access_and_our_warnings_to_one_file(tmp_path, applied):
    applied()
    logging.getLogger("uvicorn.error").info("Started server process")
    access_log = logging.getLogger("uvicorn.access")
    access_log.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "POST", "/api/speak", "1.1", 200)
    access_log.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET", "/api/health", "1.1", 200)
    logging.getLogger("pairvoice.lifecycle").warning("maintain に失敗しました")
    logging.getLogger("pairvoice.player").info("止めました（stop）: x.wav")
    logging.getLogger("httpx").info("ほかのライブラリの INFO")

    text = (tmp_path / "pairvoice.log").read_text(encoding="utf-8")
    assert "Started server process" in text
    assert '"POST /api/speak HTTP/1.1" 200' in text
    assert "/health" not in text
    assert "maintain に失敗しました" in text
    assert "止めました（stop）" in text
    assert "ほかのライブラリの INFO" not in text
    # ファイルには色を付けない
    assert "\x1b[" not in text


def test_file_config_rotates_and_keeps_old_generations(tmp_path, applied):
    applied(max_bytes=200)
    for i in range(20):
        logging.getLogger("uvicorn.error").info("line %d", i)

    assert (tmp_path / "pairvoice.log.1").exists()
    assert not (tmp_path / f"pairvoice.log.{logs.BACKUP_COUNT + 1}").exists()


def test_configure_file_writes_menubar_warnings(tmp_path):
    log_file = tmp_path / "logs" / "pairvoice-menubar.log"
    root = logging.getLogger()
    before = root.handlers[:]
    root.handlers.clear()  # basicConfig はハンドラが既にあると何もしない
    try:
        logs.configure_file(log_file)
        logging.getLogger("pairvoice.menubar").warning("studio を再起動できなかった")
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:] = before
    assert "studio を再起動できなかった" in log_file.read_text(encoding="utf-8")
