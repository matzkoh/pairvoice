from pairvoice import bundle


def test_bundle_root_prefers_studio_inside_the_package(tmp_path):
    # wheel から入れた形: パッケージの中に studio/ が同梱されている
    package = tmp_path / "site-packages" / "pairvoice"
    (package / "studio").mkdir(parents=True)
    assert bundle.bundle_root(package) == package


def test_bundle_root_falls_back_to_the_repository(tmp_path):
    # editable で入れた形: src/pairvoice/ から2つ遡ったリポジトリ直下に studio/ がある
    package = tmp_path / "repo" / "src" / "pairvoice"
    package.mkdir(parents=True)
    assert bundle.bundle_root(package) == tmp_path / "repo"


def test_bundle_root_of_this_checkout_has_studio():
    assert (bundle.BUNDLE_ROOT / "studio" / "web").is_dir()
