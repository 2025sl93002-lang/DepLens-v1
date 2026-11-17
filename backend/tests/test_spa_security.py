"""Tests for SPA static-file path-traversal protection (issue #26)."""

from pathlib import Path

from deplens.main import _resolve_spa_file


def _make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html>")
    (dist / "favicon.ico").write_text("icon")
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log(1)")
    return dist


def test_serves_legitimate_files(tmp_path):
    dist = _make_dist(tmp_path)
    assert _resolve_spa_file(dist, "favicon.ico") == (dist.resolve() / "favicon.ico")
    assert _resolve_spa_file(dist, "assets/app.js") == (dist.resolve() / "assets" / "app.js")


def test_unknown_route_falls_back_to_index(tmp_path):
    dist = _make_dist(tmp_path)
    # Client-side route with no matching file -> index.html (SPA behaviour)
    assert _resolve_spa_file(dist, "explorer") == (dist.resolve() / "index.html")
    assert _resolve_spa_file(dist, "") == (dist.resolve() / "index.html")


def test_traversal_is_blocked(tmp_path):
    dist = _make_dist(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")

    # A file that exists *outside* dist must never be served.
    for payload in ["../secret.txt", "../../secret.txt", "../../../../etc/hosts"]:
        resolved = _resolve_spa_file(dist, payload)
        assert resolved == (dist.resolve() / "index.html"), payload
        assert resolved != secret.resolve()
