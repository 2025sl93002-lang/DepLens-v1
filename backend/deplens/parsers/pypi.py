"""Parse Python dependency manifests.

Supports:
  - requirements.txt (pinned and unpinned versions)
  - pyproject.toml — [project].dependencies and [tool.poetry.dependencies]
  - Pipfile (TOML-like, via tomllib/tomli)
  - poetry.lock for resolved versions
  - Pipfile.lock (JSON) for resolved versions

Returns the same Lockfile/LockPackage/LockEdge data structures as the npm
parser so the full analysis pipeline (health, OSV, registry) is reused.
"""

from __future__ import annotations

import json as _json
import re
from typing import Dict, List, Optional, Tuple

from deplens.parsers.lockfile import ROOT_ID, LockEdge, Lockfile, LockPackage

try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]


# --- helpers -----------------------------------------------------------------

_NAME_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?:\[.*?\])?"        # extras like requests[security]
    r"(?P<spec>[^;#\n]*)"  # version spec, up to env marker or comment
    r"(?:[;#].*)?$"
)
_PIN_RE = re.compile(r"==\s*([^\s,;]+)")


def _normalize_name(name: str) -> str:
    """PEP 503 canonical name: lowercase, hyphens only."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _extract_pinned(spec: str) -> Optional[str]:
    """Return the pinned version from a spec like '==1.2.3', or None."""
    m = _PIN_RE.search(spec)
    return m.group(1) if m else None


# --- requirements.txt --------------------------------------------------------

def parse_requirements_txt(text: str) -> List[Tuple[str, str]]:
    """Return [(normalized_name, spec)] from requirements.txt text.

    Skips blank lines, comments, include flags (-r/-c/-f/--), and URL requirements.
    """
    deps: List[Tuple[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Include / index / options flags
        if re.match(r"^(-r|-c|-f|-i|--)", line):
            continue
        # URL-style requirements (VCS, direct URL)
        if "://" in line:
            continue
        m = _NAME_RE.match(line)
        if m:
            name = _normalize_name(m.group("name"))
            spec = m.group("spec").strip().rstrip(",")
            deps.append((name, spec))
    return deps


# --- pyproject.toml ----------------------------------------------------------

def _parse_pep508(dep: str) -> Tuple[str, str]:
    """Split a PEP 508 string 'name[extras] specifier ; marker' into (name, spec)."""
    # Drop environment markers
    dep = dep.split(";")[0].strip()
    m = re.match(
        r"([A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)"
        r"(?:\[.*?\])?"
        r"\s*(.*)?$",
        dep,
    )
    if m:
        return _normalize_name(m.group(1)), (m.group(3) or "").strip()
    return _normalize_name(dep), ""


def parse_pyproject_toml(text: str) -> List[Tuple[str, str]]:
    """Return [(name, spec)] from [project].dependencies or [tool.poetry.dependencies]."""
    if tomllib is None:
        return _parse_pyproject_toml_regex(text)
    try:
        data = tomllib.loads(text)
    except Exception:
        return _parse_pyproject_toml_regex(text)

    deps: List[Tuple[str, str]] = []

    # [project].dependencies — PEP 508 strings
    for dep in (data.get("project") or {}).get("dependencies") or []:
        if isinstance(dep, str):
            deps.append(_parse_pep508(dep))

    # [tool.poetry.dependencies] — {name: spec_or_dict}; skip "python"
    poetry_deps = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies", {})
    for name, spec in poetry_deps.items():
        if name.lower() == "python":
            continue
        if isinstance(spec, str):
            deps.append((_normalize_name(name), spec))
        elif isinstance(spec, dict):
            version = spec.get("version", "")
            deps.append((_normalize_name(name), str(version) if version else ""))

    return deps


def _parse_pyproject_toml_regex(text: str) -> List[Tuple[str, str]]:
    """Minimal regex fallback when tomllib is unavailable."""
    deps: List[Tuple[str, str]] = []
    in_project = False
    in_array = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            in_array = False
            continue
        if in_project:
            if re.match(r"dependencies\s*=", stripped) and not in_array:
                in_array = "[" in stripped
                continue
            if in_array:
                if "]" in stripped:
                    in_array = False
                    continue
                m = re.search(r'"([^"]+)"', stripped)
                if m:
                    deps.append(_parse_pep508(m.group(1)))
    return deps


# --- Pipfile -----------------------------------------------------------------

def parse_pipfile(text: str) -> List[Tuple[str, str]]:
    """Return [(name, spec)] from [packages] in a Pipfile."""
    if tomllib is None:
        return []
    try:
        data = tomllib.loads(text)
    except Exception:
        return []
    deps: List[Tuple[str, str]] = []
    for section in ("packages", "dev-packages"):
        for name, spec in (data.get(section) or {}).items():
            if name.lower() in ("python_version", "python_full_version"):
                continue
            if isinstance(spec, str):
                spec_str = "" if spec == "*" else spec
                deps.append((_normalize_name(name), spec_str))
            elif isinstance(spec, dict):
                version = spec.get("version", "")
                deps.append((_normalize_name(name), "" if version == "*" else str(version)))
    return deps


# --- poetry.lock / Pipfile.lock (resolved versions) -------------------------

def parse_poetry_lock(text: str) -> Dict[str, str]:
    """Return {normalized_name: resolved_version} from poetry.lock."""
    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            out: Dict[str, str] = {}
            for pkg in data.get("package") or []:
                name = pkg.get("name")
                version = pkg.get("version")
                if isinstance(name, str) and isinstance(version, str):
                    out[_normalize_name(name)] = version
            return out
        except Exception:
            pass
    return _parse_poetry_lock_regex(text)


def _parse_poetry_lock_regex(text: str) -> Dict[str, str]:
    """Regex fallback for poetry.lock (simple line-by-line)."""
    out: Dict[str, str] = {}
    name: Optional[str] = None
    version: Optional[str] = None
    for line in text.splitlines():
        m_name = re.match(r'^name\s*=\s*"([^"]+)"', line)
        m_ver = re.match(r'^version\s*=\s*"([^"]+)"', line)
        if m_name:
            name = _normalize_name(m_name.group(1))
            version = None
        if m_ver:
            version = m_ver.group(1)
        if name and version:
            out[name] = version
            name = version = None
    return out


def parse_pipfile_lock(text: str) -> Dict[str, str]:
    """Return {normalized_name: resolved_version} from Pipfile.lock (JSON)."""
    try:
        data = _json.loads(text)
    except (ValueError, TypeError):
        return {}
    out: Dict[str, str] = {}
    for section in ("default", "develop"):
        for name, info in (data.get(section) or {}).items():
            version_spec = info.get("version", "") if isinstance(info, dict) else ""
            pinned = _extract_pinned(version_spec)
            if pinned:
                out[_normalize_name(name)] = pinned
    return out


# --- Build Lockfile from Python deps -----------------------------------------

def build_pypi_lockfile(
    deps: List[Tuple[str, str]],
    resolved: Optional[Dict[str, str]] = None,
    root_name: Optional[str] = None,
    root_version: Optional[str] = None,
    fmt: str = "requirements.txt",
) -> Lockfile:
    """Convert parsed Python dependencies into a Lockfile object.

    If ``resolved`` is provided (from poetry.lock / Pipfile.lock), it maps
    package names to their resolved versions.  Otherwise we try to extract a
    pinned version from the declared spec (``==x.y.z``).  Packages without a
    pinned version are stored as ``name@unknown``.
    """
    if resolved is None:
        resolved = {}

    packages: Dict[str, LockPackage] = {}
    edges: List[LockEdge] = []
    direct: Dict[str, str] = {}
    warnings: List[str] = []
    seen: set = set()

    for name, spec in deps:
        if name in seen:
            continue
        seen.add(name)

        version = resolved.get(name) or _extract_pinned(spec)
        if version is None:
            version = "unknown"
            if spec:
                warnings.append(
                    f"{name}: version not pinned (spec: {spec!r}); showing 'unknown'"
                )
            else:
                warnings.append(f"{name}: no version specified; showing 'unknown'")

        node = LockPackage(name=name, version=version, scopes={"prod"}, is_direct=True)
        packages[node.id] = node
        edges.append(LockEdge(ROOT_ID, node.id, spec or "*", "prod"))
        direct[name] = "prod"

    if not resolved and not any(p.version != "unknown" for p in packages.values()):
        warnings.append(
            "No lockfile found — resolved versions unavailable; showing declared specs only."
        )

    return Lockfile(
        lockfile_version=0,
        fmt=fmt,
        root_name=root_name,
        root_version=root_version,
        packages=packages,
        edges=edges,
        direct=direct,
        warnings=warnings,
    )
