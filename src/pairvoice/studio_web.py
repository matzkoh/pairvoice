"""studio の画面（ビルド済みの studio/web/dist）を /studio/ の下に配る。

画面と API を同じ常駐サーバーが配るので、画面は同じオリジンで API を呼ぶ。API と画面の
道筋は同じ名前（/prompt や /profiles）なので、画面は /studio/ の下に分ける。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .bundle import BUNDLE_ROOT

STUDIO_PATH = "/studio/"
_IMMUTABLE = "public, max-age=31536000, immutable"
DIST_DIR = BUNDLE_ROOT / "studio" / "web" / "dist"

# 開発中に dist を作らずに開くと必ずここを通る。無言で壊れると「読み込み中…」で固まるのと
# 同じ迷い方をするので、次にやることを書く
_DIST_MISSING_HTML = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>pairvoice studio</title></head>
<body style="font-family: system-ui; padding: 2rem; line-height: 1.7">
<h1>ビルド成果物がありません</h1>
<p>開発中は <code>cd studio &amp;&amp; pnpm dev</code> を起動して
<a href="http://127.0.0.1:17493/studio/">http://127.0.0.1:17493/studio/</a> を開いてください。</p>
<p>ここで見たい場合は <code>cd studio &amp;&amp; pnpm build</code> を実行してください。</p>
</body></html>
"""


def open_studio(base: str) -> str:
    """base（常駐サーバーの URL）が配る studio をブラウザで開き、その URL を返す。"""
    url = f"{base}{STUDIO_PATH}"
    subprocess.Popen(["open", url], stdin=subprocess.DEVNULL)
    return url


def mount_studio(app: FastAPI, dist_dir: Path = DIST_DIR) -> None:
    root = dist_dir.resolve()

    @app.get("/", include_in_schema=False)
    @app.get("/studio", include_in_schema=False)
    def to_studio():
        return RedirectResponse(STUDIO_PATH)

    @app.get(STUDIO_PATH + "{path:path}", include_in_schema=False)
    def studio(path: str):
        index = root / "index.html"
        if not index.is_file():
            return HTMLResponse(_DIST_MISSING_HTML, status_code=503)
        asset = (root / path).resolve()
        if path and asset.is_relative_to(root) and asset.is_file():
            # assets/ はビルドのたびに中身のハッシュで名前が変わるので、ずっと覚えさせてよい
            if path.startswith("assets/"):
                return FileResponse(asset, headers={"Cache-Control": _IMMUTABLE})
            return FileResponse(asset)
        # SPA なので、実ファイルに対応しないパス（/studio/review など）も index.html を返し、
        # 画面の側でルーティングする。版を上げたら新しい index.html を読ませるため覚えさせない
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
