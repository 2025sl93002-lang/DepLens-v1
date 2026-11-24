"""Tests for workspace detection and expansion (npm/yarn/pnpm monorepos)."""

import json
from pathlib import Path

import pytest

from deplens.parsers.workspace import (
    _parse_pnpm_yaml,
    detect_workspace_patterns,
    expand_workspace_packages,
    get_workspaces,
    workspace_name_for,
)


# ---------------------------------------------------------------------------
# detect_workspace_patterns
# ---------------------------------------------------------------------------


def test_npm_workspaces_array(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "root", "workspaces": ["packages/*", "apps/*"]})
    )
    patterns = detect_workspace_patterns(tmp_path)
    assert "packages/*" in patterns
    assert "apps/*" in patterns


def test_yarn_workspaces_object(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"workspaces": {"packages": ["pkgs/*"], "nohoist": []}})
    )
    patterns = detect_workspace_patterns(tmp_path)
    assert "pkgs/*" in patterns


def test_no_workspaces_field(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "plain-project"}))
    assert detect_workspace_patterns(tmp_path) == []


def test_missing_package_json(tmp_path: Path):
    assert detect_workspace_patterns(tmp_path) == []


def test_pnpm_workspace_yaml(tmp_path: Path):
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'packages/*'\n  - 'libs/**'\n"
    )
    patterns = detect_workspace_patterns(tmp_path)
    assert "packages/*" in patterns
    assert "libs/**" in patterns


def test_pnpm_yaml_skips_negations(tmp_path: Path):
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'packages/*'\n  - '!**/test/**'\n"
    )
    patterns = detect_workspace_patterns(tmp_path)
    assert "packages/*" in patterns
    assert all(not p.startswith("!") for p in patterns)


# ---------------------------------------------------------------------------
# _parse_pnpm_yaml
# ---------------------------------------------------------------------------


def test_parse_pnpm_yaml_basic():
    yaml = "packages:\n  - 'packages/*'\n  - apps/web\n"
    patterns = _parse_pnpm_yaml(yaml)
    assert "packages/*" in patterns
    assert "apps/web" in patterns


def test_parse_pnpm_yaml_stops_at_next_key():
    yaml = "packages:\n  - 'pkgs/*'\nother:\n  - ignored\n"
    patterns = _parse_pnpm_yaml(yaml)
    assert patterns == ["pkgs/*"]


# ---------------------------------------------------------------------------
# expand_workspace_packages
# ---------------------------------------------------------------------------


def _make_ws(tmp_path: Path, rel_path: str, pkg_name: str) -> Path:
    p = tmp_path / rel_path
    p.mkdir(parents=True)
    (p / "package.json").write_text(json.dumps({"name": pkg_name}))
    return p


def test_expand_workspace_packages(tmp_path: Path):
    _make_ws(tmp_path, "packages/alpha", "@mono/alpha")
    _make_ws(tmp_path, "packages/beta", "@mono/beta")
    # A directory without package.json — should be ignored.
    (tmp_path / "packages" / "empty").mkdir()

    pkgs = expand_workspace_packages(tmp_path, ["packages/*"])
    names = {p.name for p in pkgs}
    assert "alpha" in names
    assert "beta" in names
    assert "empty" not in names


def test_expand_skips_node_modules(tmp_path: Path):
    nm = tmp_path / "node_modules" / "some-pkg"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text(json.dumps({"name": "some-pkg"}))
    pkgs = expand_workspace_packages(tmp_path, ["node_modules/*"])
    assert pkgs == []


def test_expand_no_matches(tmp_path: Path):
    pkgs = expand_workspace_packages(tmp_path, ["nonexistent/*"])
    assert pkgs == []


# ---------------------------------------------------------------------------
# get_workspaces
# ---------------------------------------------------------------------------


def test_get_workspaces_integration(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "root", "workspaces": ["packages/*"]})
    )
    _make_ws(tmp_path, "packages/lib", "@root/lib")
    _make_ws(tmp_path, "packages/app", "@root/app")

    workspaces = get_workspaces(tmp_path)
    ws_names = {ws.name for ws in workspaces}
    assert "lib" in ws_names
    assert "app" in ws_names


def test_get_workspaces_returns_empty_when_no_config(tmp_path: Path):
    (tmp_path / "package.json").write_text(json.dumps({"name": "plain"}))
    assert get_workspaces(tmp_path) == []


# ---------------------------------------------------------------------------
# workspace_name_for
# ---------------------------------------------------------------------------


def test_workspace_name_uses_package_json_name(tmp_path: Path):
    ws = tmp_path / "packages" / "lib"
    ws.mkdir(parents=True)
    (ws / "package.json").write_text(json.dumps({"name": "@mono/lib"}))
    assert workspace_name_for(tmp_path, ws) == "@mono/lib"


def test_workspace_name_falls_back_to_relative_path(tmp_path: Path):
    ws = tmp_path / "packages" / "unnamed"
    ws.mkdir(parents=True)
    # No package.json name field.
    (ws / "package.json").write_text("{}")
    name = workspace_name_for(tmp_path, ws)
    assert "unnamed" in name
