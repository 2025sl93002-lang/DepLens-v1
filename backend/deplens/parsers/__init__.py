"""Safe parsers for npm, Python and Maven project files."""

from deplens.parsers.lockfile import (
    ROOT_ID,
    LockEdge,
    Lockfile,
    LockPackage,
    parse_lockfile_text,
)
from deplens.parsers.manifest import (
    Manifest,
    ManifestDependency,
    parse_manifest_text,
)
from deplens.parsers.maven import (
    build_maven_lockfile,
    get_pom_modules,
    get_pom_root_info,
    parse_pom_xml,
)
from deplens.parsers.pypi import (
    build_pypi_lockfile,
    parse_pipfile,
    parse_pipfile_lock,
    parse_poetry_lock,
    parse_pyproject_toml,
    parse_requirements_txt,
)
from deplens.parsers.workspace import (
    detect_workspace_patterns,
    expand_workspace_packages,
    get_workspaces,
    workspace_name_for,
)

__all__ = [
    "ROOT_ID",
    "Lockfile",
    "LockEdge",
    "LockPackage",
    "parse_lockfile_text",
    "Manifest",
    "ManifestDependency",
    "parse_manifest_text",
    "build_pypi_lockfile",
    "parse_pipfile",
    "parse_pipfile_lock",
    "parse_poetry_lock",
    "parse_pyproject_toml",
    "parse_requirements_txt",
    "build_maven_lockfile",
    "get_pom_root_info",
    "get_pom_modules",
    "parse_pom_xml",
    "detect_workspace_patterns",
    "expand_workspace_packages",
    "get_workspaces",
    "workspace_name_for",
]

