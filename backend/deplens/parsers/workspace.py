"""Workspace detection for npm/yarn/pnpm monorepos.

Detects workspace configuration from:
  - root ``package.json`` ``workspaces`` field (npm, yarn)
  - ``pnpm-workspace.yaml`` ``packages`` field (pnpm)

Then glob-expands the patterns to discover all child ``package.json`` files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional


def detect_workspace_patterns(root: Path) -> List[str]:
    """Return raw workspace glob patterns from ``root``.

    Checks ``package.json`` first (npm/yarn), then ``pnpm-workspace.yaml``.
    Returns an empty list if no workspace configuration is found.
    """
    patterns: List[str] = []

    pkg_json = root / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
        except (ValueError, TypeError):
            data = {}
        ws = data.get("workspaces")
        if isinstance(ws, list):
            patterns.extend(str(p) for p in ws if isinstance(p, str))
        elif isinstance(ws, dict):
            # yarn v1: {"packages": ["packages/*"], "nohoist": [...]}
            inner = ws.get("packages")
            if isinstance(inner, list):
                patterns.extend(str(p) for p in inner if isinstance(p, str))

    if not patterns:
        pnpm_yaml = root / "pnpm-workspace.yaml"
        if pnpm_yaml.is_file():
            patterns = _parse_pnpm_yaml(pnpm_yaml.read_text(encoding="utf-8", errors="replace"))

    return patterns


def expand_workspace_packages(root: Path, patterns: List[str]) -> List[Path]:
    """Return sorted list of workspace package directories under ``root``.

    Each returned path is a directory containing a ``package.json``.  Patterns
    follow ``glob`` syntax (``*``, ``**``, ``?``).  Directories whose name
    starts with ``.`` or that live inside ``node_modules`` are skipped.
    """
    result: List[Path] = []
    seen: set = set()

    for pattern in patterns:
        # Strip trailing slash so glob works consistently.
        pattern = pattern.rstrip("/")
        try:
            matches = sorted(root.glob(pattern))
        except (ValueError, OSError):
            continue
        for candidate in matches:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen:
                continue
            # Must be a real directory inside root.
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            if not resolved.is_dir():
                continue
            # Skip node_modules and hidden dirs.
            parts = resolved.relative_to(root).parts
            if any(p.startswith(".") or p == "node_modules" for p in parts):
                continue
            if (resolved / "package.json").is_file():
                seen.add(resolved)
                result.append(resolved)

    return result


def get_workspaces(root: Path) -> List[Path]:
    """Detect workspace config and expand to concrete package directories.

    Returns an empty list if the project has no workspace configuration.
    """
    patterns = detect_workspace_patterns(root)
    if not patterns:
        return []
    return expand_workspace_packages(root, patterns)


# -- pnpm-workspace.yaml minimal parser -------------------------------------
# pnpm-workspace.yaml only uses a simple "packages:" list, no need for a full
# YAML parser.  Example:
#   packages:
#     - 'packages/*'
#     - 'apps/**'
#     - '!**/test/**'   <- exclusion (skip)

def _parse_pnpm_yaml(text: str) -> List[str]:
    """Extract package patterns from a pnpm-workspace.yaml file."""
    patterns: List[str] = []
    in_packages = False
    for line in text.splitlines():
        stripped = line.strip()
        if re.match(r"^packages\s*:", stripped):
            in_packages = True
            continue
        if in_packages:
            # A new top-level key means we left the packages block.
            if stripped and not stripped.startswith("-") and re.match(r"^[a-zA-Z]", stripped):
                break
            m = re.match(r"^\s*-\s*['\"]?(?P<pat>[^'\"#\n]+)['\"]?\s*(?:#.*)?$", line)
            if m:
                pat = m.group("pat").strip()
                if pat and not pat.startswith("!"):  # skip negations
                    patterns.append(pat)
    return patterns


def workspace_name_for(workspace_root: Path, workspace_path: Path) -> Optional[str]:
    """Return the workspace display name for a child package.

    Reads ``package.json`` ``name`` if present, otherwise uses the directory
    name relative to the monorepo root.
    """
    pkg_json = workspace_path / "package.json"
    if pkg_json.is_file():
        try:
            data = json.loads(pkg_json.read_text(encoding="utf-8", errors="replace"))
            name = data.get("name")
            if isinstance(name, str) and name:
                return name
        except (ValueError, TypeError):
            pass
    try:
        return str(workspace_path.relative_to(workspace_root))
    except ValueError:
        return workspace_path.name
