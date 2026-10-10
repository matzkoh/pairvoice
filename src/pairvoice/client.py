"""常駐サーバーの HTTP API を叩く。CLI と menubar が使う。"""

from __future__ import annotations

import json
from urllib import request

from .config import API_PREFIX


def call(
    base: str,
    path: str,
    *,
    method: str = "POST",
    body: dict | None = None,
    timeout: float = 30,
) -> dict:
    """pairvoice の API（path は API_PREFIX を除いた道筋）を叩いて応答を返す。

    timeout は呼ぶ側が選ぶ（menubar は短くする）。
    """
    payload = json.dumps(body or {}).encode() if method == "POST" else None
    req = request.Request(
        f"{base}{API_PREFIX}{path}",
        data=payload,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode())
