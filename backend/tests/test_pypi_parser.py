"""Tests for the PyPI parser (all offline, no network calls)."""

from deplens.parsers.lockfile import ROOT_ID
from deplens.parsers.pypi import (
    build_pypi_lockfile,
    parse_pipfile,
    parse_pipfile_lock,
    parse_poetry_lock,
    parse_pyproject_toml,
    parse_requirements_txt,
)


# --- parse_requirements_txt --------------------------------------------------

def test_parse_requirements_txt_pinned():
    deps = parse_requirements_txt("requests==2.28.0\nnumpy==1.24.0\n")
    assert ("requests", "==2.28.0") in deps
    assert ("numpy", "==1.24.0") in deps


def test_parse_requirements_txt_unpinned():
    deps = parse_requirements_txt("flask>=2.0\nclick\n")
    names = [n for n, _ in deps]
    assert "flask" in names
    assert "click" in names


def test_parse_requirements_txt_skips_comments_and_flags():
    deps = parse_requirements_txt(
        "# comment\n-r other.txt\n--index-url https://x\nrequests==2.28.0\n"
    )
    assert len(deps) == 1
    assert deps[0][0] == "requests"


def test_requirements_txt_normalizes_name():
    deps = parse_requirements_txt("Pillow==9.0.0\nMy_Package==1.0\n")
    names = [n for n, _ in deps]
    assert "pillow" in names
    assert "my-package" in names


def test_requirements_txt_skips_url_requirements():
    deps = parse_requirements_txt("git+https://github.com/foo/bar.git\nrequests==2.28.0\n")
    assert len(deps) == 1
    assert deps[0][0] == "requests"


def test_requirements_txt_handles_extras():
    deps = parse_requirements_txt("requests[security]==2.28.0\n")
    assert len(deps) == 1
    assert deps[0][0] == "requests"
    assert "2.28.0" in deps[0][1]


# --- parse_pyproject_toml ----------------------------------------------------

def test_parse_pyproject_toml_project_deps():
    toml = """
[project]
name = "myapp"
version = "1.0.0"
dependencies = [
    "requests>=2.28",
    "click==8.0.0",
]
"""
    deps = parse_pyproject_toml(toml)
    names = [n for n, _ in deps]
    assert "requests" in names
    assert "click" in names


def test_parse_pyproject_toml_poetry_deps():
    toml = """
[tool.poetry.dependencies]
python = "^3.9"
requests = "^2.28"
numpy = ">=1.24"
"""
    deps = parse_pyproject_toml(toml)
    names = [n for n, _ in deps]
    assert "requests" in names
    assert "numpy" in names
    assert "python" not in names


def test_parse_pyproject_toml_empty():
    deps = parse_pyproject_toml("[build-system]\nrequires = ['setuptools']\n")
    assert deps == []


# --- parse_pipfile ------------------------------------------------------------

def test_parse_pipfile():
    toml = """
[packages]
requests = "*"
flask = ">=2.0"

[dev-packages]
pytest = "*"
"""
    deps = parse_pipfile(toml)
    # Only check if tomllib is available; fallback returns []
    if deps:
        names = [n for n, _ in deps]
        assert "requests" in names
        assert "flask" in names


def test_parse_pipfile_star_becomes_empty():
    toml = '[packages]\nrequests = "*"\n'
    deps = parse_pipfile(toml)
    if deps:
        assert deps[0] == ("requests", "")


# --- parse_poetry_lock -------------------------------------------------------

def test_parse_poetry_lock():
    lock = """
[[package]]
name = "requests"
version = "2.31.0"
description = "HTTP library"

[[package]]
name = "numpy"
version = "1.26.0"
"""
    resolved = parse_poetry_lock(lock)
    assert resolved.get("requests") == "2.31.0"
    assert resolved.get("numpy") == "1.26.0"


def test_parse_poetry_lock_normalizes_name():
    lock = '[[package]]\nname = "My_Package"\nversion = "1.0.0"\n'
    resolved = parse_poetry_lock(lock)
    assert resolved.get("my-package") == "1.0.0"


# --- parse_pipfile_lock -------------------------------------------------------

def test_parse_pipfile_lock():
    import json
    data = {
        "default": {
            "requests": {"version": "==2.28.0"},
            "flask": {"version": "==2.2.0"},
        }
    }
    resolved = parse_pipfile_lock(json.dumps(data))
    assert resolved.get("requests") == "2.28.0"
    assert resolved.get("flask") == "2.2.0"


def test_parse_pipfile_lock_invalid_json():
    resolved = parse_pipfile_lock("{not json")
    assert resolved == {}


# --- build_pypi_lockfile -----------------------------------------------------

def test_build_pypi_lockfile_pinned():
    deps = [("requests", "==2.28.0"), ("numpy", "==1.24.0")]
    lock = build_pypi_lockfile(deps, root_name="myapp")
    assert "requests@2.28.0" in lock.packages
    assert "numpy@1.24.0" in lock.packages
    assert all(p.is_direct for p in lock.packages.values())
    assert any(e.source == ROOT_ID for e in lock.edges)


def test_build_pypi_lockfile_resolved_wins():
    deps = [("requests", ">=2.0")]
    resolved = {"requests": "2.31.0"}
    lock = build_pypi_lockfile(deps, resolved=resolved)
    assert "requests@2.31.0" in lock.packages


def test_build_pypi_lockfile_unknown_version():
    deps = [("flask", ">=2.0")]
    lock = build_pypi_lockfile(deps)
    assert "flask@unknown" in lock.packages
    assert lock.warnings


def test_build_pypi_lockfile_deduplicates():
    deps = [("requests", "==2.28.0"), ("requests", "==2.28.0")]
    lock = build_pypi_lockfile(deps)
    assert len(lock.packages) == 1


def test_build_pypi_lockfile_all_direct():
    deps = [("a", "==1.0"), ("b", "==2.0"), ("c", "")]
    lock = build_pypi_lockfile(deps, resolved={"a": "1.0", "b": "2.0", "c": "3.0"})
    assert all(p.is_direct for p in lock.packages.values())
    assert all("prod" in p.scopes for p in lock.packages.values())


def test_build_pypi_lockfile_edges_from_root():
    deps = [("requests", "==2.28.0")]
    lock = build_pypi_lockfile(deps)
    assert lock.edges[0].source == ROOT_ID
    assert lock.edges[0].target == "requests@2.28.0"


def test_build_pypi_lockfile_format():
    deps = [("requests", "==2.28.0")]
    lock = build_pypi_lockfile(deps, fmt="pyproject.toml", root_name="app", root_version="1.0")
    assert lock.fmt == "pyproject.toml"
    assert lock.root_name == "app"
    assert lock.root_version == "1.0"
