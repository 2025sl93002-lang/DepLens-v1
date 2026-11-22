"""Parse Maven pom.xml dependency manifests.

Supports:
  - Single-module pom.xml (direct dependencies)
  - Multi-module projects (parent pom with <modules>)
  - Property substitution (${spring.version})
  - dependencyManagement version inheritance
  - Scope filtering (test/system/provided marked as dev)

Returns the same Lockfile/LockPackage/LockEdge structures so the full
analysis pipeline (health, OSV, registry) is reused unchanged.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

from deplens.parsers.lockfile import ROOT_ID, LockEdge, Lockfile, LockPackage


def _ns_aware_find(el: ET.Element, tag: str) -> Optional[ET.Element]:
    """Find child element with or without XML namespace prefix."""
    result = el.find(tag)
    if result is not None:
        return result
    ns = _extract_ns(el.tag)
    if ns:
        return el.find(f"{{{ns}}}{tag}")
    return None


def _ns_aware_findall(el: ET.Element, tag: str) -> List[ET.Element]:
    result = el.findall(tag)
    if result:
        return result
    ns = _extract_ns(el.tag)
    if ns:
        return el.findall(f"{{{ns}}}{tag}")
    return []


def _extract_ns(tag: str) -> str:
    if tag.startswith("{"):
        return tag[1 : tag.index("}")]
    return ""


def _child_text(el: ET.Element, tag: str, default: str = "") -> str:
    child = _ns_aware_find(el, tag)
    return (child.text or "").strip() if child is not None else default


def _resolve(value: str, props: Dict[str, str]) -> str:
    """Expand ${property} references, up to 5 levels deep."""
    for _ in range(5):
        m = re.search(r"\$\{([^}]+)\}", value)
        if not m:
            break
        key = m.group(1)
        replacement = props.get(key, "")
        value = value[: m.start()] + replacement + value[m.end() :]
    return value.strip()


def _collect_properties(root_el: ET.Element) -> Dict[str, str]:
    props: Dict[str, str] = {}
    # Project coordinates as built-in properties
    for key in ("groupId", "artifactId", "version"):
        val = _child_text(root_el, key)
        if val:
            props[f"project.{key}"] = val
    # <properties> block
    props_el = _ns_aware_find(root_el, "properties")
    if props_el is not None:
        for child in list(props_el):
            local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            props[local] = (child.text or "").strip()
    return props


def _collect_managed(root_el: ET.Element, props: Dict[str, str]) -> Dict[str, str]:
    managed: Dict[str, str] = {}
    dm = _ns_aware_find(root_el, "dependencyManagement")
    if dm is None:
        return managed
    deps_el = _ns_aware_find(dm, "dependencies")
    if deps_el is None:
        return managed
    for dep in _ns_aware_findall(deps_el, "dependency"):
        g = _resolve(_child_text(dep, "groupId"), props)
        a = _resolve(_child_text(dep, "artifactId"), props)
        v = _resolve(_child_text(dep, "version"), props)
        if g and a and v:
            managed[f"{g}:{a}"] = v
    return managed


def parse_pom_xml(text: str) -> List[Tuple[str, str, bool]]:
    """Parse a pom.xml string.

    Returns list of (groupId:artifactId, version, is_dev) tuples.
    is_dev is True for test/provided/system scopes.
    Version may be "unknown" when not resolvable.
    """
    try:
        root_el = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"pom.xml is not valid XML: {exc}") from exc

    props = _collect_properties(root_el)
    managed = _collect_managed(root_el, props)

    deps: List[Tuple[str, str, bool]] = []
    seen: set = set()

    deps_el = _ns_aware_find(root_el, "dependencies")
    if deps_el is None:
        return deps

    for dep in _ns_aware_findall(deps_el, "dependency"):
        g = _resolve(_child_text(dep, "groupId"), props)
        a = _resolve(_child_text(dep, "artifactId"), props)
        v = _resolve(_child_text(dep, "version"), props)
        scope = _child_text(dep, "scope") or "compile"
        dep_type = _child_text(dep, "type") or "jar"

        if not g or not a:
            continue
        if dep_type == "pom" and scope == "import":
            continue  # BOM imports — not a runtime dependency

        pkg_name = f"{g}:{a}"
        if pkg_name in seen:
            continue
        seen.add(pkg_name)

        if not v:
            v = managed.get(pkg_name, "unknown")
        if not v:
            v = "unknown"

        is_dev = scope in ("test", "provided", "system")
        deps.append((pkg_name, v, is_dev))

    return deps


def build_maven_lockfile(
    deps: List[Tuple[str, str, bool]],
    root_name: Optional[str] = None,
    root_version: Optional[str] = None,
) -> Lockfile:
    """Convert parsed Maven dependencies into a Lockfile."""
    packages: Dict[str, LockPackage] = {}
    edges: List[LockEdge] = []
    direct: Dict[str, str] = {}

    for pkg_name, version, is_dev in deps:
        scope = "dev" if is_dev else "prod"
        node = LockPackage(
            name=pkg_name,
            version=version,
            scopes={scope},
            is_direct=True,
        )
        packages[node.id] = node
        edges.append(LockEdge(ROOT_ID, node.id, version, scope))
        direct[pkg_name] = scope

    return Lockfile(
        lockfile_version=0,
        fmt="pom.xml",
        root_name=root_name,
        root_version=root_version,
        packages=packages,
        edges=edges,
        direct=direct,
        warnings=[],
    )


def get_pom_root_info(text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (artifactId, version) from a pom.xml string."""
    try:
        root_el = ET.fromstring(text)
    except ET.ParseError:
        return None, None
    props = _collect_properties(root_el)
    group_id = _resolve(_child_text(root_el, "groupId"), props)
    artifact_id = _resolve(_child_text(root_el, "artifactId"), props)
    version = _resolve(_child_text(root_el, "version"), props)
    name = f"{group_id}:{artifact_id}" if group_id and artifact_id else artifact_id or None
    return name, version or None


def get_pom_modules(text: str) -> List[str]:
    """Return list of <module> paths from a parent pom.xml."""
    try:
        root_el = ET.fromstring(text)
    except ET.ParseError:
        return []
    modules_el = _ns_aware_find(root_el, "modules")
    if modules_el is None:
        return []
    result = []
    for mod in _ns_aware_findall(modules_el, "module"):
        if mod.text:
            result.append(mod.text.strip())
    return result
