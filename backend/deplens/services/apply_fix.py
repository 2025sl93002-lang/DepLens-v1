"""Apply-fix: turn remediation targets into a concrete manifest edit.

Two modes, both of which NEVER run any install process:

* ``compute_fix`` — produce the exact edit, a unified diff, and the new file
  content, WITHOUT touching disk (powers copy / download / patch).
* ``write_fix`` — write the updated manifest to disk after validating the path,
  keeping a timestamped ``.bak`` backup.

Supported ecosystems:
  - npm/yarn/pnpm: edits package.json (direct deps) or adds overrides (transitive)
  - pypi: edits requirements.txt, pyproject.toml, or Pipfile in-place
  - maven: not yet supported (pom.xml edits require XML manipulation)
"""

from __future__ import annotations

import difflib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from deplens.analyzers.remediation import plan_remediation
from deplens.db import Database
from deplens.services.safety import PathSafetyError, is_within, resolve_project_root

DEP_FIELDS = ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]

PYPI_MANIFESTS = ["requirements.txt", "pyproject.toml", "Pipfile"]


@dataclass
class FixChange:
    package: str
    kind: str  # "dependency" | "override"
    field: Optional[str]  # which manifest section (for dependency)
    from_spec: Optional[str]
    to_spec: str


@dataclass
class FixEdit:
    manifest_path: str
    changes: List[FixChange] = field(default_factory=list)
    diff: str = ""
    old_content: str = ""
    new_content: str = ""
    install_command: str = "npm install"
    warnings: List[str] = field(default_factory=list)
    written: bool = False
    backup_path: Optional[str] = None


class ApplyError(Exception):
    pass


def _bump_spec(original: Optional[str], target: str) -> str:
    """Produce a new range for ``target`` preserving the original operator style."""
    if original and re.match(r"^\^\d", original):
        return f"^{target}"
    if original and re.match(r"^~\d", original):
        return f"~{target}"
    if original and re.match(r"^\d", original):  # exact pin
        return target
    # Non-standard spec (range, url, tag) → default to caret.
    return f"^{target}"


def _detect_indent(text: str):
    for line in text.splitlines():
        stripped = line.lstrip(" \t")
        if stripped and line != stripped:
            prefix = line[: len(line) - len(stripped)]
            return "\t" if "\t" in prefix else len(prefix)
    return 2


def _get_project_and_root(db: Database, scan_id: int):
    scan = db.query_one("SELECT project_id FROM scans WHERE id=?", (scan_id,))
    if scan is None:
        raise ApplyError("Scan not found")
    project = db.get_project(scan["project_id"])
    if project is None:
        raise ApplyError("Project not found")
    try:
        root = resolve_project_root(project["path"])
    except PathSafetyError as exc:
        raise ApplyError(f"Project path is no longer accessible: {exc}") from exc
    return project, root


def _manifest_path(db: Database, scan_id: int) -> Path:
    _, root = _get_project_and_root(db, scan_id)
    manifest = root / "package.json"
    if not manifest.is_file() or not is_within(root, manifest):
        raise ApplyError("package.json not found in the project directory")
    return manifest


def _pypi_manifest_path(root: Path) -> Path:
    for name in PYPI_MANIFESTS:
        p = root / name
        if p.is_file():
            return p
    raise ApplyError(
        f"No Python manifest found in the project directory "
        f"({', '.join(PYPI_MANIFESTS)})"
    )


# ---------------------------------------------------------------------------
# Python (PyPI) fix logic
# ---------------------------------------------------------------------------

_VERSION_OPS = re.compile(r"[><=!~^]+")
_PKG_LINE = re.compile(
    r"^(\s*)"                        # leading whitespace
    r"([A-Za-z0-9][\w.\-]*)"        # package name
    r"(\[[\w,\s]*\])?"              # optional extras [security,...]
    r"([^\s#;]*)"                    # version spec
    r"(.*)?$",                       # rest of line (markers, comments)
    re.IGNORECASE,
)


def _normalize_pypi(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _fix_requirements_txt(old: str, upgrades: dict) -> tuple[str, list[FixChange]]:
    """Apply version upgrades to requirements.txt content."""
    changes: list[FixChange] = []
    lines = old.splitlines(keepends=True)
    new_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("-"):
            new_lines.append(line)
            continue

        m = _PKG_LINE.match(line.rstrip("\n"))
        if not m:
            new_lines.append(line)
            continue

        leading, raw_name, extras, spec, rest = m.groups()
        norm = _normalize_pypi(raw_name)
        target = upgrades.get(norm) or upgrades.get(raw_name)
        if target is None:
            new_lines.append(line)
            continue

        new_spec = f"=={target}"
        new_line = f"{leading}{raw_name}{extras or ''}{new_spec}{rest or ''}\n"
        changes.append(
            FixChange(raw_name, "dependency", "requirements.txt", spec.strip() or None, new_spec)
        )
        new_lines.append(new_line)

    return "".join(new_lines), changes


def _fix_pyproject_toml(old: str, upgrades: dict) -> tuple[str, list[FixChange]]:
    """Apply version upgrades to pyproject.toml by line-level replacement."""
    changes: list[FixChange] = []
    lines = old.splitlines(keepends=True)
    new_lines = []
    in_deps = False

    for line in lines:
        stripped = line.strip()
        # Section header detection
        if stripped.startswith("["):
            in_deps = (
                stripped in ("[project]", "[tool.poetry.dependencies]", "[packages]")
                or stripped.startswith("[project.")
            )
            new_lines.append(line)
            continue

        if not in_deps:
            new_lines.append(line)
            continue

        # Try to match a dependency line: name = ">=1.0" or "name>=1.0"
        # PEP 508 inline: 'requests>=2.28'
        pep508 = re.match(
            r'^(\s*")([A-Za-z0-9][\w.\-]*)(\[[\w,\s]*\])?([^"]*)"(.*)', line
        )
        toml_assign = re.match(
            r'^(\s*)([A-Za-z0-9][\w.\-]*)(\s*=\s*)"([^"]*)"(.*)', line
        )

        matched = False
        if pep508:
            lead, name, extras, spec, tail = pep508.groups()
            norm = _normalize_pypi(name)
            target = upgrades.get(norm) or upgrades.get(name)
            if target:
                new_spec = f">={target}"
                new_line = f'{lead}{name}{extras or ""}{new_spec}"{tail}\n'
                changes.append(
                    FixChange(name, "dependency", "pyproject.toml", spec.strip(), new_spec)
                )
                new_lines.append(new_line)
                matched = True
        elif toml_assign:
            lead, name, eq, spec, tail = toml_assign.groups()
            norm = _normalize_pypi(name)
            target = upgrades.get(norm) or upgrades.get(name)
            if target:
                new_spec = f">={target}"
                new_line = f'{lead}{name}{eq}"{new_spec}"{tail}\n'
                changes.append(
                    FixChange(name, "dependency", "pyproject.toml", spec.strip(), new_spec)
                )
                new_lines.append(new_line)
                matched = True

        if not matched:
            new_lines.append(line)

    return "".join(new_lines), changes


def _fix_pipfile(old: str, upgrades: dict) -> tuple[str, list[FixChange]]:
    """Apply version upgrades to Pipfile TOML content."""
    changes: list[FixChange] = []
    lines = old.splitlines(keepends=True)
    new_lines = []
    in_packages = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_packages = stripped in ("[packages]", "[dev-packages]")
            new_lines.append(line)
            continue

        if not in_packages:
            new_lines.append(line)
            continue

        m = re.match(r'^(\s*)([A-Za-z0-9][\w.\-]*)(\s*=\s*)"([^"]*)"(.*)', line)
        if m:
            lead, name, eq, spec, tail = m.groups()
            norm = _normalize_pypi(name)
            target = upgrades.get(norm) or upgrades.get(name)
            if target:
                new_spec = f"=={target}"
                new_lines.append(f'{lead}{name}{eq}"{new_spec}"{tail}\n')
                changes.append(FixChange(name, "dependency", "Pipfile", spec.strip(), new_spec))
                continue

        new_lines.append(line)

    return "".join(new_lines), changes


def compute_fix(db: Database, scan_id: int, packages: Optional[List[str]] = None) -> FixEdit:
    project, root = _get_project_and_root(db, scan_id)
    ecosystem = project.get("ecosystem", "npm")

    if ecosystem == "pypi":
        return _compute_fix_pypi(db, scan_id, root, packages)
    if ecosystem == "maven":
        raise ApplyError(
            "Automatic fix is not yet supported for Maven projects. Update pom.xml manually."
        )
    return _compute_fix_npm(db, scan_id, root, packages)


def _compute_fix_npm(
    db: Database, scan_id: int, root: Path, packages: Optional[List[str]]
) -> FixEdit:
    manifest = root / "package.json"
    if not manifest.is_file() or not is_within(root, manifest):
        raise ApplyError("package.json not found in the project directory")

    old_content = manifest.read_text(encoding="utf-8")
    try:
        data = json.loads(old_content)
    except json.JSONDecodeError as exc:
        raise ApplyError(f"package.json is not valid JSON: {exc}") from exc

    plan = plan_remediation(db, scan_id)
    wanted = set(packages) if packages else None
    actions = [
        a for a in plan.actions
        if a.target_version and (wanted is None or a.package in wanted)
    ]

    edit = FixEdit(manifest_path=str(manifest), old_content=old_content)
    if not actions:
        edit.warnings.append("No applicable fixes (no fixable advisories selected).")
        edit.new_content = old_content
        return edit

    overrides = dict(data.get("overrides") or {})
    for a in actions:
        if a.strategy == "direct":
            placed = False
            for fieldname in DEP_FIELDS:
                section = data.get(fieldname)
                if isinstance(section, dict) and a.package in section:
                    new_spec = _bump_spec(section[a.package], a.target_version)
                    edit.changes.append(
                        FixChange(a.package, "dependency", fieldname,
                                  section[a.package], new_spec)
                    )
                    section[a.package] = new_spec
                    placed = True
                    break
            if not placed:
                overrides[a.package] = a.target_version
                edit.changes.append(
                    FixChange(a.package, "override", None, None, a.target_version)
                )
        else:
            prev = overrides.get(a.package)
            overrides[a.package] = a.target_version
            edit.changes.append(
                FixChange(a.package, "override", None, prev, a.target_version)
            )

    if overrides:
        data["overrides"] = overrides
        edit.warnings.append(
            "Transitive fixes use npm `overrides`, which require npm 8.3+."
        )

    indent = _detect_indent(old_content)
    new_content = json.dumps(data, indent=indent, ensure_ascii=False)
    if old_content.endswith("\n"):
        new_content += "\n"
    edit.new_content = new_content
    edit.install_command = "npm install"
    edit.diff = "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile="package.json",
            tofile="package.json (proposed)",
        )
    )
    return edit


def _compute_fix_pypi(
    db: Database, scan_id: int, root: Path, packages: Optional[List[str]]
) -> FixEdit:
    manifest = _pypi_manifest_path(root)
    old_content = manifest.read_text(encoding="utf-8")

    plan = plan_remediation(db, scan_id)
    wanted = set(packages) if packages else None
    actions = [
        a for a in plan.actions
        if a.target_version and (wanted is None or a.package in wanted)
    ]

    edit = FixEdit(manifest_path=str(manifest), old_content=old_content)
    if not actions:
        edit.warnings.append("No applicable fixes (no fixable advisories selected).")
        edit.new_content = old_content
        return edit

    upgrades = {a.package: a.target_version for a in actions}

    name = manifest.name
    if name == "requirements.txt":
        new_content, changes = _fix_requirements_txt(old_content, upgrades)
        edit.install_command = "pip install -r requirements.txt"
    elif name == "pyproject.toml":
        new_content, changes = _fix_pyproject_toml(old_content, upgrades)
        edit.install_command = "pip install -e ."
    elif name == "Pipfile":
        new_content, changes = _fix_pipfile(old_content, upgrades)
        edit.install_command = "pipenv install"
    else:
        raise ApplyError(f"Unsupported Python manifest: {name}")

    not_applied = set(upgrades.keys()) - {c.package for c in changes}
    for pkg in not_applied:
        edit.warnings.append(
            f"{pkg}: not found in {name} — add it manually or it may be a transitive dependency."
        )

    edit.changes = changes
    edit.new_content = new_content
    edit.diff = "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=name,
            tofile=f"{name} (proposed)",
        )
    )
    return edit


def write_fix(db: Database, scan_id: int, packages: Optional[List[str]] = None) -> FixEdit:
    edit = compute_fix(db, scan_id, packages)
    if not edit.changes:
        return edit
    manifest = Path(edit.manifest_path)
    suffix = manifest.suffix  # .json, .txt, .toml, etc.
    backup = manifest.with_suffix(f"{suffix}.bak-{int(time.time())}")
    backup.write_text(edit.old_content, encoding="utf-8")
    manifest.write_text(edit.new_content, encoding="utf-8")
    edit.written = True
    edit.backup_path = str(backup)
    return edit


def edit_to_dict(edit: FixEdit) -> dict:
    return json.loads(json.dumps(asdict(edit)))
