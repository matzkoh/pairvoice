from fastapi import FastAPI
from fastapi.testclient import TestClient

from pairvoice.studio_web import mount_studio
from tests.test_server import build


def client_for(dist):
    app = FastAPI()
    mount_studio(app, dist)
    return TestClient(app, base_url="http://127.0.0.1:17495")


def make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<title>studio</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text("secret")
    return dist


def test_root_and_bare_path_redirect_to_studio(tmp_path):
    client = client_for(make_dist(tmp_path))

    for path in ("/", "/studio"):
        response = client.get(path, follow_redirects=False)
        assert (response.status_code, response.headers["location"]) == (307, "/studio/")


def test_serves_assets_and_falls_back_to_index_for_spa_routes(tmp_path):
    client = client_for(make_dist(tmp_path))

    asset = client.get("/studio/assets/app.js")
    assert asset.text == "console.log(1)"
    assert "javascript" in asset.headers["content-type"]
    # ハッシュ付きの名前なので、ずっと覚えさせてよい
    assert "immutable" in asset.headers["cache-control"]
    for path in ("/studio/", "/studio/review", "/studio/profiles/new"):
        page = client.get(path)
        assert page.text == "<title>studio</title>"
        assert page.headers["cache-control"] == "no-cache"


def test_does_not_serve_files_outside_dist(tmp_path):
    client = client_for(make_dist(tmp_path))

    response = client.get("/studio/%2E%2E/secret.txt")

    assert "secret" not in response.text


def test_guides_to_build_when_dist_is_missing(tmp_path):
    response = client_for(tmp_path / "missing").get("/studio/review")

    assert response.status_code == 503
    assert "pnpm build" in response.text


def test_api_routes_win_and_studio_stays_out_of_the_api_docs():
    _, client = build()

    assert client.get("/health").headers["content-type"] == "application/json"
    assert all(
        not path.startswith("/studio/{") for path in client.get("/openapi.json").json()["paths"]
    )


def test_studio_is_refused_to_foreign_pages():
    _, client = build()

    response = client.get("/studio/", headers={"host": "evil.example"})

    assert response.status_code == 403
