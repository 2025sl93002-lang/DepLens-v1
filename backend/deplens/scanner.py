"""Scan orchestration: the SCAN stage of DepLens.

Ties the parsers, graph engine and analyzers together into a single scan, runs
each stage with honest success/partial/failed/skipped status, computes the
health score, and persists everything to SQLite. Nothing here executes project
code; the only outbound calls are opt-in registry/OSV metadata lookups.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from deplens.analyzers import health, licenses, maintenance, usage, versions
from deplens.db import Database
from deplens.parsers import parse_lockfile_text, parse_manifest_text
from deplens.parsers.lockfile import ROOT_ID
from deplens.parsers.maven import build_maven_lockfile, get_pom_root_info, parse_pom_xml
from deplens.parsers.pypi import (
    build_pypi_lockfile,
    parse_pipfile,
    parse_pipfile_lock,
    parse_poetry_lock,
    parse_pyproject_toml,
    parse_requirements_txt,
)
from deplens.parsers.workspace import get_workspaces, workspace_name_for
from deplens.services.graph import build_graph
from deplens.services.osv import SEVERITY_ORDER, OSVClient
from deplens.services.registry import MavenRegistryClient, PyPIRegistryClient, RegistryClient
from deplens.services.safety import SKIP_DIRS, is_within, resolve_project_root

MANIFEST = "package.json"
LOCKFILES = ["package-lock.json", "npm-shrinkwrap.json"]


class ScanError(Exception):
    """Raised when a scan cannot start (e.g. no manifest found)."""


def _detect_ecosystem(d: Path) -> Optional[str]:
    """Return the ecosystem of a folder from its manifest, or None."""
    if (d / MANIFEST).is_file():
        return "npm"
    for f in PYPI_MANIFESTS:
        if (d / f).is_file():
            return "pypi"
    if (d / MAVEN_MANIFEST).is_file():
        return "maven"
    return None


def _project_entry(root: Path, d: Path, ecosystem: str) -> Dict[str, Any]:
    """Describe a discovered project folder for the New-scan picker."""
    name = d.name
    if ecosystem == "npm":
        try:
            text = (d / MANIFEST).read_text(encoding="utf-8", errors="replace")
            name = parse_manifest_text(text).name or d.name
        except ValueError:
            pass
    return {
        "path": str(d),
        "name": name,
        "rel": "." if d == root else d.relative_to(root).as_posix(),
        "ecosystem": ecosystem,
    }


def discover_projects(
    path: str, max_depth: int = 3, max_results: int = 100
) -> List[Dict[str, Any]]:
    """Find scannable projects at or below ``path``.

    If the selected folder has a supported manifest (npm, Python or Maven) it is
    returned alone. Otherwise the folder is searched (bounded depth, skipping
    node_modules and build output) for subfolders that contain one — each is a
    candidate the caller can pick. Descent stops at the first manifest on a
    branch, so a project's own subfolders are not listed separately.
    """
    root = resolve_project_root(path)
    eco = _detect_ecosystem(root)
    if eco:
        return [_project_entry(root, root, eco)]

    results: List[Dict[str, Any]] = []
    root_depth = len(root.parts)
    for dirpath, dirnames, _filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        depth = len(dp.parts) - root_depth
        dirnames[:] = [
            x
            for x in dirnames
            if x not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, x))
        ]
        if depth >= max_depth:
            dirnames[:] = []
        if dp != root and is_within(root, dp):
            sub_eco = _detect_ecosystem(dp)
            if sub_eco:
                results.append(_project_entry(root, dp, sub_eco))
                dirnames[:] = []  # a project's own subfolders are part of it
                if len(results) >= max_results:
                    break
    results.sort(key=lambda r: r["rel"])
    return results


@dataclass
class Stage:
    key: str
    label: str
    status: str = "pending"  # pending | ok | partial | skipped | failed
    detail: str = ""
    duration_ms: int = 0


@dataclass
class ScanContext:
    path: str
    online: bool
    stages: List[Stage] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def stage(self, key: str, label: str) -> Stage:
        s = Stage(key=key, label=label)
        self.stages.append(s)
        return s


def _primary_scope(scopes: List[str]) -> str:
    for s in ("prod", "optional", "peer", "dev"):
        if s in scopes:
            return s
    return "prod"


async def run_scan(db: Database, path: str, online: bool = True) -> int:
    """Run a full scan of the npm project at ``path`` and persist it.

    Returns the new scan id. Auto-detects workspaces and delegates to
    ``run_workspace_scan`` when a monorepo configuration is found.
    """
    # Peek for workspace config before starting the full pipeline.
    try:
        root = resolve_project_root(path)
    except Exception:
        root = None
    if root is not None and (root / MANIFEST).is_file():
        workspace_paths = get_workspaces(root)
        if workspace_paths:
            return await run_workspace_scan(db, path, online)
    ctx = ScanContext(path=path, online=online)
    started = time.time()

    # Stage 1 — discover project ------------------------------------------------
    st = ctx.stage("discover", "Discovering project")
    t0 = time.time()
    root = resolve_project_root(path)
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        st.status = "failed"
        st.detail = "No package.json found in the selected directory."
        raise ScanError(st.detail)
    lock_path = None
    for lf in LOCKFILES:
        if (root / lf).is_file():
            lock_path = root / lf
            break
    st.status = "ok"
    st.detail = f"Found {MANIFEST}" + (f" and {lock_path.name}" if lock_path else "")
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 2 — read manifests --------------------------------------------------
    st = ctx.stage("manifest", "Reading manifests")
    t0 = time.time()
    manifest = parse_manifest_text(manifest_path.read_text(encoding="utf-8", errors="replace"))
    ctx.warnings.extend(manifest.warnings)
    project_name = manifest.name or root.name
    st.status = "ok"
    st.detail = f"{len(manifest.declared())} declared dependencies"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 3 — resolve dependency graph ---------------------------------------
    st = ctx.stage("resolve", "Resolving dependency graph")
    t0 = time.time()
    if lock_path is None:
        st.status = "partial"
        st.detail = (
            "No lockfile found. Only direct dependencies from package.json are available; "
            "the transitive graph cannot be resolved without a lockfile."
        )
        lockfile = _manifest_only_lockfile(manifest)
    else:
        try:
            lockfile = parse_lockfile_text(lock_path.read_text(encoding="utf-8", errors="replace"))
            ctx.warnings.extend(lockfile.warnings)
            st.status = "ok" if not lockfile.warnings else "partial"
            st.detail = (
                f"{len(lockfile.packages)} packages, {len(lockfile.edges)} edges "
                f"(lockfile {lockfile.fmt})"
            )
        except ValueError as exc:
            st.status = "failed"
            st.detail = str(exc)
            lockfile = _manifest_only_lockfile(manifest)
    graph = build_graph(lockfile)
    st.duration_ms = int((time.time() - t0) * 1000)

    package_names = sorted({p.name for p in lockfile.packages.values()})

    # Stage 4 — registry metadata (updates / license / maintenance) ------------
    registry_info: Dict[str, Any] = {}
    registry_ratio = 0.0
    st = ctx.stage("registry", "Analyzing versions & metadata")
    t0 = time.time()
    if online and package_names:
        try:
            async with RegistryClient() as reg:
                registry_info = await reg.get_many(package_names)
            available = sum(1 for i in registry_info.values() if i.available)
            registry_ratio = available / len(package_names) if package_names else 0.0
            st.status = "ok" if registry_ratio >= 0.95 else "partial"
            st.detail = f"Fetched metadata for {available}/{len(package_names)} packages"
        except Exception as exc:  # network / unexpected
            st.status = "failed"
            st.detail = f"Registry unavailable: {exc}"
    else:
        st.status = "skipped"
        st.detail = "Offline mode — update, license and maintenance data not fetched."
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 5 — security advisories (OSV) --------------------------------------
    vulns_by_pkg: Dict[str, list] = {}
    security_checked = False
    st = ctx.stage("security", "Checking security advisories")
    t0 = time.time()
    if online and lockfile.packages:
        try:
            query = [(p.name, p.version) for p in lockfile.packages.values()]
            async with OSVClient() as osv:
                vulns_by_pkg = await osv.query_packages(query)
            security_checked = True
            total_vulns = sum(len(v) for v in vulns_by_pkg.values())
            st.status = "ok"
            st.detail = f"{total_vulns} advisories across {len(vulns_by_pkg)} package(s)"
        except Exception as exc:
            st.status = "failed"
            st.detail = f"OSV unavailable: {exc}. Security status is UNKNOWN, not secure."
    else:
        st.status = "skipped"
        st.detail = "Offline mode — security not checked. Status is UNKNOWN, not secure."
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 6 — license analysis ------------------------------------------------
    st = ctx.stage("license", "Analyzing licenses")
    t0 = time.time()
    policy = db.get_setting("license_policy", None)
    license_results = {}
    for name in package_names:
        info = registry_info.get(name)
        raw = info.license if info else None
        license_results[name] = licenses.evaluate_license(name, raw, policy)
    st.status = "ok" if online else "partial"
    st.detail = f"Classified {len(license_results)} package licenses"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 7 — usage analysis (offline, local source) -------------------------
    st = ctx.stage("usage", "Checking package usage")
    t0 = time.time()
    try:
        usage_results = usage.analyze_usage(root, manifest)
        st.status = "ok"
        st.detail = f"Analyzed usage of {len(usage_results)} direct dependencies"
    except Exception as exc:
        usage_results = {}
        st.status = "failed"
        st.detail = f"Usage analysis failed: {exc}"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 8 — build findings & health ----------------------------------------
    st = ctx.stage("findings", "Generating health findings")
    t0 = time.time()
    maint_results = {
        name: maintenance.analyze_maintenance(name, registry_info.get(name))
        for name in package_names
    }
    update_results = {
        name: versions.analyze_update(
            name,
            _version_of(name, lockfile),
            registry_info.get(name),
        )
        for name in package_names
    }

    vuln_severity_counts: Counter = Counter()
    for vlist in vulns_by_pkg.values():
        for v in vlist:
            vuln_severity_counts[v.severity] += 1
    outdated_counts: Counter = Counter()
    for _name, u in update_results.items():
        if u.is_outdated and u.update_type in ("major", "minor", "patch"):
            outdated_counts[u.update_type] += 1
    license_counts: Counter = Counter()
    for lr in license_results.values():
        if lr.verdict == "restricted":
            license_counts["restricted"] += 1
        elif lr.verdict == "review":
            license_counts["review" if lr.family != "unknown" else "unknown"] += 1
    unused_count = sum(1 for u in usage_results.values() if u.status == "none")
    maintenance_count = sum(
        1 for m in maint_results.values() if m.status in ("concerns", "deprecated")
    )

    health_score = health.compute_health(
        vuln_counts=dict(vuln_severity_counts),
        outdated_counts=dict(outdated_counts),
        license_counts=dict(license_counts),
        unused_count=unused_count,
        maintenance_count=maintenance_count,
        security_checked=security_checked,
        registry_available_ratio=registry_ratio if online else 0.0,
    )
    st.status = "ok"
    st.detail = f"Health score {health_score.score} ({health_score.grade})"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 9 — persist ---------------------------------------------------------
    st = ctx.stage("persist", "Preparing report")
    t0 = time.time()
    for name, info in registry_info.items():
        if info and info.description:
            _DESC_CACHE[name] = info.description
    summary = _build_summary(
        graph, lockfile, vulns_by_pkg, update_results, license_results, usage_results,
        maint_results, vuln_severity_counts,
    )
    project_id = db.upsert_project(project_name, str(root), "npm")
    scan_id = _persist(
        db, project_id, ctx, lockfile, graph, vulns_by_pkg, update_results,
        license_results, usage_results, maint_results, health_score, summary,
    )
    st.status = "ok"
    st.detail = f"Saved scan #{scan_id}"
    st.duration_ms = int((time.time() - t0) * 1000)

    ctx.warnings.append(f"Scan completed in {int((time.time() - started) * 1000)} ms")
    # Re-save the finalized stages/warnings (the persist stage finalizes after
    # the initial row is written, so update it now).
    db.execute(
        "UPDATE scans SET stages_json=?, warnings_json=? WHERE id=?",
        (
            json.dumps([asdict(s) for s in ctx.stages]),
            json.dumps(ctx.warnings),
            scan_id,
        ),
    )
    return scan_id


def _version_of(name: str, lockfile) -> str:
    for p in lockfile.packages.values():
        if p.name == name:
            return p.version
    return "0.0.0"


def _manifest_only_lockfile(manifest):
    """Build a minimal lockfile from the manifest alone (no transitive data)."""
    from deplens.parsers.lockfile import LockEdge, Lockfile, LockPackage

    packages = {}
    edges = []
    direct = {}
    for dep in manifest.declared().values():
        # Without a lockfile we do not know the resolved version.
        node = LockPackage(name=dep.name, version="unknown", scopes={dep.scope}, is_direct=True)
        packages[node.id] = node
        edges.append(LockEdge(ROOT_ID, node.id, dep.spec, dep.scope))
        direct[dep.name] = dep.scope
    return Lockfile(
        lockfile_version=0, fmt="manifest-only", root_name=manifest.name,
        root_version=manifest.version, packages=packages, edges=edges, direct=direct,
        warnings=["No lockfile: resolved versions and transitive dependencies are unavailable."],
    )


def _build_summary(
    graph, lockfile, vulns_by_pkg, update_results, license_results, usage_results,
    maint_results, vuln_severity_counts,
) -> Dict[str, Any]:
    total = len(lockfile.packages)
    direct = len(graph.direct_ids())
    scope_counts: Counter = Counter()
    for p in lockfile.packages.values():
        scope_counts[_primary_scope(sorted(p.scopes))] += 1
    update_dist: Counter = Counter(u.update_type for u in update_results.values())
    license_family_dist: Counter = Counter(lr.family for lr in license_results.values())
    vulnerable_packages = len([k for k, v in vulns_by_pkg.items() if v])
    return {
        "total": total,
        "direct": direct,
        "transitive": total - direct,
        "scope_distribution": dict(scope_counts),
        "outdated": sum(1 for u in update_results.values() if u.is_outdated),
        "update_distribution": dict(update_dist),
        "vulnerable_packages": vulnerable_packages,
        "severity_distribution": dict(vuln_severity_counts),
        "license_distribution": dict(license_family_dist),
        "unknown_licenses": sum(1 for lr in license_results.values() if lr.family == "unknown"),
        "license_warnings": sum(
            1 for lr in license_results.values() if lr.verdict in ("review", "restricted")
        ),
        "potentially_unused": sum(1 for u in usage_results.values() if u.status == "none"),
        "maintenance_concerns": sum(
            1 for m in maint_results.values() if m.status in ("concerns", "deprecated")
        ),
    }


def _persist(
    db, project_id, ctx, lockfile, graph, vulns_by_pkg, update_results,
    license_results, usage_results, maint_results, health_score, summary,
    workspace_map: Optional[Dict[str, str]] = None,
) -> int:
    cur = db.execute(
        "INSERT INTO scans(project_id, created_at, status, online, lockfile_format, "
        "root_name, root_version, health_score, health_grade, health_confidence, "
        "summary_json, stages_json, warnings_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            project_id, time.time(), "completed", int(ctx.online), lockfile.fmt,
            lockfile.root_name, lockfile.root_version, health_score.score,
            health_score.grade, health_score.confidence,
            json.dumps({**summary, "health": asdict(health_score)}),
            json.dumps([asdict(s) for s in ctx.stages]),
            json.dumps(ctx.warnings),
        ),
    )
    scan_id = cur.lastrowid

    pkg_rows = []
    for node in graph.nodes.values():
        name = node.name
        u = update_results.get(name)
        lr = license_results.get(name)
        mr = maint_results.get(name)
        ur = usage_results.get(name) if node.is_direct else None
        pkg_vulns = vulns_by_pkg.get(node.id, [])
        max_sev = _max_severity([v.severity for v in pkg_vulns])
        data = {
            "update": asdict(u) if u else None,
            "license": asdict(lr) if lr else None,
            "maintenance": asdict(mr) if mr else None,
            "usage": _usage_to_dict(ur) if ur else None,
            "scopes": node.scopes,
            "description": _desc(name),
        }
        pkg_rows.append(
            (
                scan_id, node.id, name, node.version, int(node.is_direct),
                _primary_scope(node.scopes), node.depth, node.dependents, node.path_count,
                u.update_type if u else None, u.latest if u else None,
                lr.license if lr else None, lr.family if lr else None,
                lr.verdict if lr else None,
                ur.status if ur else None, mr.status if mr else None,
                max_sev, len(pkg_vulns),
                workspace_map.get(node.id) if workspace_map else None,
                json.dumps(data),
            )
        )
    db.executemany(
        "INSERT INTO packages(scan_id, node_id, name, version, is_direct, scope, depth, "
        "dependents, path_count, update_type, latest, license, license_family, "
        "license_verdict, usage_status, maintenance_status, max_severity, vuln_count, "
        "workspace_name, data_json) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        pkg_rows,
    )

    db.executemany(
        "INSERT INTO edges(scan_id, source, target, edge_type, spec) VALUES (?,?,?,?,?)",
        [(scan_id, e.source, e.target, e.edge_type, e.spec) for e in lockfile.edges],
    )

    vuln_rows = []
    for vlist in vulns_by_pkg.values():
        for v in vlist:
            url = v.references[0] if v.references else f"https://osv.dev/vulnerability/{v.id}"
            vuln_rows.append(
                (
                    scan_id, v.package, v.installed_version, v.id, v.cve, v.severity,
                    v.summary, v.fixed_version, url, v.published, json.dumps(asdict(v)),
                )
            )
    if vuln_rows:
        db.executemany(
            "INSERT INTO vulnerabilities(scan_id, package, installed_version, osv_id, cve, "
            "severity, summary, fixed_version, url, published, data_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            vuln_rows,
        )
    return scan_id


def _usage_to_dict(ur) -> dict:
    return {
        "package": ur.package,
        "status": ur.status,
        "confidence": ur.confidence,
        "explanation": ur.explanation,
        "evidence": [asdict(e) for e in ur.evidence],
    }


def _max_severity(sevs: List[str]) -> Optional[str]:
    best = None
    best_rank = -1
    for s in sevs:
        rank = SEVERITY_ORDER.get(s, 0)
        if rank > best_rank:
            best_rank = rank
            best = s
    return best


_DESC_CACHE: Dict[str, str] = {}


def _desc(name: str) -> Optional[str]:
    return _DESC_CACHE.get(name)


# ---------------------------------------------------------------------------
# PyPI scanner
# ---------------------------------------------------------------------------

PYPI_MANIFESTS = ["requirements.txt", "pyproject.toml", "Pipfile"]
PYPI_LOCKFILES = ["poetry.lock", "Pipfile.lock"]


async def run_pypi_scan(db: Database, path: str, online: bool = True) -> int:
    """Run a full scan of the Python project at ``path`` and persist it.

    Returns the new scan id.
    """
    ctx = ScanContext(path=path, online=online)
    started = time.time()

    # Stage 1 — discover --------------------------------------------------------
    st = ctx.stage("discover", "Discovering project")
    t0 = time.time()
    root = resolve_project_root(path)
    manifest_path = None
    manifest_type = None
    for name in PYPI_MANIFESTS:
        p = root / name
        if p.is_file():
            manifest_path = p
            manifest_type = name
            break
    if manifest_path is None:
        st.status = "failed"
        st.detail = "No Python manifest found (requirements.txt, pyproject.toml, or Pipfile)."
        raise ScanError(st.detail)
    lock_path = None
    lock_type = None
    for name in PYPI_LOCKFILES:
        p = root / name
        if p.is_file():
            lock_path = p
            lock_type = name
            break
    st.status = "ok"
    st.detail = f"Found {manifest_type}" + (f" and {lock_type}" if lock_type else "")
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 2 — parse manifest --------------------------------------------------
    st = ctx.stage("manifest", "Reading manifests")
    t0 = time.time()
    text = manifest_path.read_text(encoding="utf-8", errors="replace")
    root_name = root.name
    root_version = None
    if manifest_type == "requirements.txt":
        deps = parse_requirements_txt(text)
    elif manifest_type == "pyproject.toml":
        deps = parse_pyproject_toml(text)
        try:
            import tomllib as _tl
        except ImportError:
            try:
                import tomli as _tl  # type: ignore[no-redef]
            except ImportError:
                _tl = None  # type: ignore[assignment]
        if _tl is not None:
            try:
                _data = _tl.loads(text)
                root_name = (_data.get("project") or {}).get("name") or root.name
                root_version = (_data.get("project") or {}).get("version")
                # poetry project name/version
                if root_name == root.name:
                    _poetry = (_data.get("tool") or {}).get("poetry") or {}
                    root_name = _poetry.get("name") or root.name
                    root_version = _poetry.get("version") or root_version
            except Exception:
                pass
    else:  # Pipfile
        deps = parse_pipfile(text)
    st.status = "ok"
    st.detail = f"{len(deps)} declared dependencies"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 3 — resolve versions ------------------------------------------------
    st = ctx.stage("resolve", "Resolving dependency versions")
    t0 = time.time()
    resolved: Dict[str, str] = {}
    if lock_path is not None:
        try:
            lock_text = lock_path.read_text(encoding="utf-8", errors="replace")
            if lock_type == "poetry.lock":
                resolved = parse_poetry_lock(lock_text)
            else:
                resolved = parse_pipfile_lock(lock_text)
        except Exception as exc:
            ctx.warnings.append(f"Could not parse {lock_type}: {exc}")
    lockfile = build_pypi_lockfile(
        deps, resolved=resolved or None,
        root_name=root_name, root_version=root_version,
        fmt=manifest_type or "requirements.txt",
    )
    ctx.warnings.extend(lockfile.warnings)
    if lock_path is not None and resolved:
        st.status = "ok"
        st.detail = f"{len(lockfile.packages)} packages resolved from {lock_type}"
    else:
        st.status = "partial"
        st.detail = (
            f"{len(lockfile.packages)} packages from {manifest_type}; "
            "no lockfile — transitive graph unavailable."
        )
    graph = build_graph(lockfile)
    st.duration_ms = int((time.time() - t0) * 1000)

    package_names = sorted({p.name for p in lockfile.packages.values()})

    # Stage 4 — registry (PyPI) -------------------------------------------------
    registry_info: Dict[str, Any] = {}
    registry_ratio = 0.0
    st = ctx.stage("registry", "Analyzing versions & metadata")
    t0 = time.time()
    if online and package_names:
        try:
            async with PyPIRegistryClient() as reg:
                registry_info = await reg.get_many(package_names)
            available = sum(1 for i in registry_info.values() if i.available)
            registry_ratio = available / len(package_names) if package_names else 0.0
            st.status = "ok" if registry_ratio >= 0.95 else "partial"
            st.detail = f"Fetched metadata for {available}/{len(package_names)} packages"
        except Exception as exc:
            st.status = "failed"
            st.detail = f"PyPI registry unavailable: {exc}"
    else:
        st.status = "skipped"
        st.detail = "Offline mode — update, license and maintenance data not fetched."
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 5 — security (OSV, PyPI ecosystem) ----------------------------------
    vulns_by_pkg: Dict[str, list] = {}
    security_checked = False
    st = ctx.stage("security", "Checking security advisories")
    t0 = time.time()
    if online and lockfile.packages:
        query = [
            (p.name, p.version)
            for p in lockfile.packages.values()
            if p.version != "unknown"
        ]
        if query:
            try:
                async with OSVClient() as osv:
                    vulns_by_pkg = await osv.query_packages(query, ecosystem="PyPI")
                security_checked = True
                total_vulns = sum(len(v) for v in vulns_by_pkg.values())
                st.status = "ok"
                st.detail = f"{total_vulns} advisories across {len(vulns_by_pkg)} package(s)"
            except Exception as exc:
                st.status = "failed"
                st.detail = f"OSV unavailable: {exc}. Security status is UNKNOWN, not secure."
        else:
            st.status = "skipped"
            st.detail = "No pinned versions — cannot query security advisories."
    else:
        st.status = "skipped"
        st.detail = "Offline mode — security not checked. Status is UNKNOWN, not secure."
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 6 — license ---------------------------------------------------------
    st = ctx.stage("license", "Analyzing licenses")
    t0 = time.time()
    policy = db.get_setting("license_policy", None)
    license_results = {}
    for name in package_names:
        info = registry_info.get(name)
        raw = info.license if info else None
        license_results[name] = licenses.evaluate_license(name, raw, policy)
    st.status = "ok" if online else "partial"
    st.detail = f"Classified {len(license_results)} package licenses"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 7 — usage (skipped for Python) -------------------------------------
    st = ctx.stage("usage", "Checking package usage")
    usage_results: Dict[str, Any] = {}
    st.status = "skipped"
    st.detail = "Usage analysis not yet supported for Python projects."
    st.duration_ms = 0

    # Stage 8 — findings & health -----------------------------------------------
    st = ctx.stage("findings", "Generating health findings")
    t0 = time.time()
    maint_results = {
        name: maintenance.analyze_maintenance(name, registry_info.get(name))
        for name in package_names
    }
    update_results = {
        name: versions.analyze_update(
            name,
            _version_of(name, lockfile),
            registry_info.get(name),
        )
        for name in package_names
    }

    vuln_severity_counts: Counter = Counter()
    for vlist in vulns_by_pkg.values():
        for v in vlist:
            vuln_severity_counts[v.severity] += 1
    outdated_counts: Counter = Counter()
    for _name, u in update_results.items():
        if u.is_outdated and u.update_type in ("major", "minor", "patch"):
            outdated_counts[u.update_type] += 1
    license_counts: Counter = Counter()
    for lr in license_results.values():
        if lr.verdict == "restricted":
            license_counts["restricted"] += 1
        elif lr.verdict == "review":
            license_counts["review" if lr.family != "unknown" else "unknown"] += 1

    health_score = health.compute_health(
        vuln_counts=dict(vuln_severity_counts),
        outdated_counts=dict(outdated_counts),
        license_counts=dict(license_counts),
        unused_count=0,
        maintenance_count=sum(
            1 for m in maint_results.values() if m.status in ("concerns", "deprecated")
        ),
        security_checked=security_checked,
        registry_available_ratio=registry_ratio if online else 0.0,
    )
    st.status = "ok"
    st.detail = f"Health score {health_score.score} ({health_score.grade})"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 9 — persist ---------------------------------------------------------
    st = ctx.stage("persist", "Preparing report")
    t0 = time.time()
    for name, info in registry_info.items():
        if info and info.description:
            _DESC_CACHE[name] = info.description
    summary = _build_summary(
        graph, lockfile, vulns_by_pkg, update_results, license_results, usage_results,
        maint_results, vuln_severity_counts,
    )
    project_id = db.upsert_project(root_name, str(root), "pypi")
    scan_id = _persist(
        db, project_id, ctx, lockfile, graph, vulns_by_pkg, update_results,
        license_results, usage_results, maint_results, health_score, summary,
    )
    st.status = "ok"
    st.detail = f"Saved scan #{scan_id}"
    st.duration_ms = int((time.time() - t0) * 1000)

    ctx.warnings.append(f"Scan completed in {int((time.time() - started) * 1000)} ms")
    db.execute(
        "UPDATE scans SET stages_json=?, warnings_json=? WHERE id=?",
        (
            json.dumps([asdict(s) for s in ctx.stages]),
            json.dumps(ctx.warnings),
            scan_id,
        ),
    )
    return scan_id


# ---------------------------------------------------------------------------
# Maven scanner
# ---------------------------------------------------------------------------

MAVEN_MANIFEST = "pom.xml"


async def run_maven_scan(db: Database, path: str, online: bool = True) -> int:
    """Run a full scan of the Maven project at ``path`` and persist it.

    Returns the new scan id.
    """
    ctx = ScanContext(path=path, online=online)
    started = time.time()

    # Stage 1 — discover --------------------------------------------------------
    st = ctx.stage("discover", "Discovering Maven project")
    t0 = time.time()
    root = resolve_project_root(path)
    manifest_path = root / MAVEN_MANIFEST
    if not manifest_path.is_file():
        st.status = "failed"
        st.detail = "No pom.xml found."
        raise ScanError(st.detail)
    st.status = "ok"
    st.detail = "Found pom.xml"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 2 — parse pom.xml ---------------------------------------------------
    st = ctx.stage("manifest", "Parsing pom.xml")
    t0 = time.time()
    text = manifest_path.read_text(encoding="utf-8", errors="replace")
    root_name, root_version = get_pom_root_info(text)
    if root_name is None:
        root_name = root.name
    deps = parse_pom_xml(text)
    lockfile = build_maven_lockfile(deps, root_name=root_name, root_version=root_version)
    graph = build_graph(lockfile)
    package_names = sorted({p.name for p in lockfile.packages.values()})
    st.status = "ok"
    st.detail = f"{len(deps)} declared dependencies"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 3 — registry (Maven Central, online only) ---------------------------
    registry_info: Dict[str, Any] = {}
    registry_ratio = 0.0
    st = ctx.stage("registry", "Fetching Maven Central metadata")
    t0 = time.time()
    if online and package_names:
        try:
            async with MavenRegistryClient() as reg:
                registry_info = await reg.get_many(package_names)
            available = sum(1 for i in registry_info.values() if i.available)
            registry_ratio = available / len(package_names) if package_names else 0.0
            st.status = "ok" if registry_ratio >= 0.95 else "partial"
            st.detail = f"Fetched metadata for {available}/{len(package_names)} packages"
        except Exception as exc:
            st.status = "failed"
            st.detail = f"Maven Central unavailable: {exc}"
    else:
        st.status = "skipped"
        st.detail = "Offline mode — update, license and maintenance data not fetched."
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 4 — security (OSV, Maven ecosystem) ---------------------------------
    vulns_by_pkg: Dict[str, list] = {}
    security_checked = False
    st = ctx.stage("security", "Checking security advisories")
    t0 = time.time()
    if online and lockfile.packages:
        query = [
            (p.name, p.version)
            for p in lockfile.packages.values()
            if p.version not in ("unknown", "")
        ]
        if query:
            try:
                async with OSVClient() as osv:
                    vulns_by_pkg = await osv.query_packages(query, ecosystem="Maven")
                security_checked = True
                total_vulns = sum(len(v) for v in vulns_by_pkg.values())
                st.status = "ok"
                st.detail = f"{total_vulns} advisories across {len(vulns_by_pkg)} package(s)"
            except Exception as exc:
                st.status = "failed"
                st.detail = f"OSV unavailable: {exc}. Security status is UNKNOWN, not secure."
        else:
            st.status = "skipped"
            st.detail = "No pinned versions — cannot query security advisories."
    else:
        st.status = "skipped"
        st.detail = "Offline mode — security not checked. Status is UNKNOWN, not secure."
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 5 — license ---------------------------------------------------------
    st = ctx.stage("license", "Analyzing licenses")
    t0 = time.time()
    policy = db.get_setting("license_policy", None)
    license_results = {}
    for name in package_names:
        info = registry_info.get(name)
        raw = info.license if info else None
        license_results[name] = licenses.evaluate_license(name, raw, policy)
    st.status = "ok" if online else "partial"
    st.detail = f"Classified {len(license_results)} package licenses"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 6 — usage (skipped for Maven) --------------------------------------
    usage_results: Dict[str, Any] = {}
    st = ctx.stage("usage", "Checking package usage")
    st.status = "skipped"
    st.detail = "Usage analysis not supported for Maven projects."
    st.duration_ms = 0

    # Stage 7 — findings & health -----------------------------------------------
    st = ctx.stage("findings", "Generating health findings")
    t0 = time.time()
    maint_results = {
        name: maintenance.analyze_maintenance(name, registry_info.get(name))
        for name in package_names
    }
    update_results = {
        name: versions.analyze_update(
            name,
            _version_of(name, lockfile),
            registry_info.get(name),
        )
        for name in package_names
    }

    vuln_severity_counts: Counter = Counter()
    for vlist in vulns_by_pkg.values():
        for v in vlist:
            vuln_severity_counts[v.severity] += 1
    outdated_counts: Counter = Counter()
    for _name, u in update_results.items():
        if u.is_outdated and u.update_type in ("major", "minor", "patch"):
            outdated_counts[u.update_type] += 1
    license_counts: Counter = Counter()
    for lr in license_results.values():
        if lr.verdict == "restricted":
            license_counts["restricted"] += 1
        elif lr.verdict == "review":
            license_counts["review" if lr.family != "unknown" else "unknown"] += 1

    health_score = health.compute_health(
        vuln_counts=dict(vuln_severity_counts),
        outdated_counts=dict(outdated_counts),
        license_counts=dict(license_counts),
        unused_count=0,
        maintenance_count=sum(
            1 for m in maint_results.values() if m.status in ("concerns", "deprecated")
        ),
        security_checked=security_checked,
        registry_available_ratio=registry_ratio if online else 0.0,
    )
    st.status = "ok"
    st.detail = f"Health score {health_score.score} ({health_score.grade})"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 8 — persist ---------------------------------------------------------
    st = ctx.stage("persist", "Preparing report")
    t0 = time.time()
    summary = _build_summary(
        graph, lockfile, vulns_by_pkg, update_results, license_results, usage_results,
        maint_results, vuln_severity_counts,
    )
    project_id = db.upsert_project(root_name, str(root), "maven")
    scan_id = _persist(
        db, project_id, ctx, lockfile, graph, vulns_by_pkg, update_results,
        license_results, usage_results, maint_results, health_score, summary,
    )
    st.status = "ok"
    st.detail = f"Saved scan #{scan_id}"
    st.duration_ms = int((time.time() - t0) * 1000)

    ctx.warnings.append(f"Scan completed in {int((time.time() - started) * 1000)} ms")
    db.execute(
        "UPDATE scans SET stages_json=?, warnings_json=? WHERE id=?",
        (
            json.dumps([asdict(s) for s in ctx.stages]),
            json.dumps(ctx.warnings),
            scan_id,
        ),
    )
    return scan_id


# ---------------------------------------------------------------------------
# Auto-detect scanner
# ---------------------------------------------------------------------------

async def auto_scan(db: Database, path: str, online: bool = True) -> int:
    """Auto-detect project type and run the appropriate scanner.

    - npm: ``package.json`` present
    - pypi: ``requirements.txt``, ``pyproject.toml``, or ``Pipfile`` present
    - maven: ``pom.xml`` present

    Returns the new scan id.
    """
    root = resolve_project_root(path)
    if (root / "package.json").is_file():
        return await run_scan(db, path, online)
    for f in PYPI_MANIFESTS:
        if (root / f).is_file():
            return await run_pypi_scan(db, path, online)
    if (root / MAVEN_MANIFEST).is_file():
        return await run_maven_scan(db, path, online)
    raise ScanError(
        "No supported manifest found. Expected package.json (npm/yarn/pnpm), "
        "requirements.txt or pyproject.toml (Python), or pom.xml (Maven)."
    )


async def run_workspace_scan(db: Database, path: str, online: bool = True) -> int:
    """Scan an npm/yarn/pnpm workspace monorepo and persist a combined scan.

    Each child workspace contributes its direct dependencies; they all share
    the root lockfile for resolved transitive versions.  Packages are tagged
    with the workspace that lists them as a direct dependency so the UI can
    break down results per-workspace.

    Returns the new scan id.
    """
    ctx = ScanContext(path=path, online=online)
    started = time.time()

    # Stage 1 — discover --------------------------------------------------------
    st = ctx.stage("discover", "Discovering workspace")
    t0 = time.time()
    root = resolve_project_root(path)
    manifest_path = root / MANIFEST
    if not manifest_path.is_file():
        st.status = "failed"
        st.detail = "No package.json found in workspace root."
        raise ScanError(st.detail)
    lock_path = None
    for lf in LOCKFILES:
        if (root / lf).is_file():
            lock_path = root / lf
            break
    workspace_paths = get_workspaces(root)
    st.status = "ok"
    st.detail = (
        f"Detected {len(workspace_paths)} workspace package(s)"
        + (f"; {lock_path.name}" if lock_path else "; no lockfile")
    )
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 2 — read manifests --------------------------------------------------
    st = ctx.stage("manifest", "Reading workspace manifests")
    t0 = time.time()
    root_manifest = parse_manifest_text(
        manifest_path.read_text(encoding="utf-8", errors="replace")
    )
    ctx.warnings.extend(root_manifest.warnings)
    project_name = root_manifest.name or root.name

    # Collect per-workspace manifests and build direct-dep → workspace mapping.
    # workspace_direct: package_name -> workspace_name
    workspace_manifests: Dict[str, Any] = {}
    workspace_direct: Dict[str, str] = {}
    for ws_path in workspace_paths:
        ws_name = workspace_name_for(root, ws_path)
        ws_pkg_json = ws_path / MANIFEST
        if ws_pkg_json.is_file():
            try:
                ws_manifest = parse_manifest_text(
                    ws_pkg_json.read_text(encoding="utf-8", errors="replace")
                )
                workspace_manifests[ws_name] = ws_manifest
                for dep_name in ws_manifest.declared():
                    # First workspace to declare wins (avoid overwrite on shared deps).
                    if dep_name not in workspace_direct:
                        workspace_direct[dep_name] = ws_name
            except ValueError:
                ctx.warnings.append(f"Workspace {ws_name}: could not parse package.json")

    total_declared = sum(len(m.declared()) for m in workspace_manifests.values())
    st.status = "ok"
    st.detail = f"{len(workspace_manifests)} workspaces, {total_declared} total declared deps"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Stage 3 — resolve dependency graph ----------------------------------------
    st = ctx.stage("resolve", "Resolving workspace dependency graph")
    t0 = time.time()
    if lock_path is None:
        st.status = "partial"
        st.detail = (
            "No root lockfile found. Only direct dependencies are available; "
            "transitive graph cannot be resolved."
        )
        # Merge all workspace manifests into a combined manifest-only lockfile.
        all_deps = []
        for ws_manifest in workspace_manifests.values():
            all_deps.extend(ws_manifest.declared().values())
        combined_manifest = _merge_manifest_deps(root_manifest, all_deps)
        lockfile = _manifest_only_lockfile(combined_manifest)
    else:
        try:
            lockfile = parse_lockfile_text(
                lock_path.read_text(encoding="utf-8", errors="replace")
            )
            ctx.warnings.extend(lockfile.warnings)
            st.status = "ok" if not lockfile.warnings else "partial"
            st.detail = (
                f"{len(lockfile.packages)} packages, {len(lockfile.edges)} edges "
                f"(lockfile {lockfile.fmt})"
            )
        except ValueError as exc:
            st.status = "failed"
            st.detail = str(exc)
            combined_manifest = root_manifest
            lockfile = _manifest_only_lockfile(combined_manifest)
    graph = build_graph(lockfile)
    st.duration_ms = int((time.time() - t0) * 1000)

    # Build workspace_map: node_id -> workspace_name for packages in
    # the lockfile whose name is a direct dep of some workspace.
    workspace_map: Dict[str, str] = {}
    for pkg in lockfile.packages.values():
        ws = workspace_direct.get(pkg.name)
        if ws:
            workspace_map[pkg.id] = ws

    package_names = sorted({p.name for p in lockfile.packages.values()})

    # Stages 4–8 are identical to run_scan —————————————————————————————————————
    registry_info: Dict[str, Any] = {}
    registry_ratio = 0.0
    st = ctx.stage("registry", "Analyzing versions & metadata")
    t0 = time.time()
    if online and package_names:
        try:
            async with RegistryClient() as reg:
                registry_info = await reg.get_many(package_names)
            available = sum(1 for i in registry_info.values() if i.available)
            registry_ratio = available / len(package_names)
            st.status = "ok" if registry_ratio >= 0.95 else "partial"
            st.detail = f"Fetched metadata for {available}/{len(package_names)} packages"
        except Exception as exc:
            st.status = "failed"
            st.detail = f"Registry unavailable: {exc}"
    else:
        st.status = "skipped"
        st.detail = "Offline mode — update, license and maintenance data not fetched."
    st.duration_ms = int((time.time() - t0) * 1000)

    vulns_by_pkg: Dict[str, list] = {}
    security_checked = False
    st = ctx.stage("security", "Checking security advisories")
    t0 = time.time()
    if online and lockfile.packages:
        try:
            query = [(p.name, p.version) for p in lockfile.packages.values()]
            async with OSVClient() as osv:
                vulns_by_pkg = await osv.query_packages(query)
            security_checked = True
            total_vulns = sum(len(v) for v in vulns_by_pkg.values())
            st.status = "ok"
            st.detail = f"{total_vulns} advisories across {len(vulns_by_pkg)} package(s)"
        except Exception as exc:
            st.status = "failed"
            st.detail = f"OSV unavailable: {exc}. Security status is UNKNOWN, not secure."
    else:
        st.status = "skipped"
        st.detail = "Offline mode — security not checked. Status is UNKNOWN, not secure."
    st.duration_ms = int((time.time() - t0) * 1000)

    st = ctx.stage("license", "Analyzing licenses")
    t0 = time.time()
    policy = db.get_setting("license_policy", None)
    license_results = {}
    for name in package_names:
        info = registry_info.get(name)
        raw = info.license if info else None
        license_results[name] = licenses.evaluate_license(name, raw, policy)
    st.status = "ok" if online else "partial"
    st.detail = f"Classified {len(license_results)} package licenses"
    st.duration_ms = int((time.time() - t0) * 1000)

    st = ctx.stage("usage", "Checking package usage")
    t0 = time.time()
    try:
        usage_results = usage.analyze_usage(root, root_manifest)
        st.status = "ok"
        st.detail = f"Analyzed usage of {len(usage_results)} direct dependencies"
    except Exception as exc:
        usage_results = {}
        st.status = "failed"
        st.detail = f"Usage analysis failed: {exc}"
    st.duration_ms = int((time.time() - t0) * 1000)

    st = ctx.stage("findings", "Generating health findings")
    t0 = time.time()
    maint_results = {
        name: maintenance.analyze_maintenance(name, registry_info.get(name))
        for name in package_names
    }
    update_results = {
        name: versions.analyze_update(
            name, _version_of(name, lockfile), registry_info.get(name)
        )
        for name in package_names
    }

    vuln_severity_counts: Counter = Counter()
    for vlist in vulns_by_pkg.values():
        for v in vlist:
            vuln_severity_counts[v.severity] += 1
    outdated_counts: Counter = Counter()
    for _name, u in update_results.items():
        if u.is_outdated and u.update_type in ("major", "minor", "patch"):
            outdated_counts[u.update_type] += 1
    license_counts: Counter = Counter()
    for lr in license_results.values():
        if lr.verdict == "restricted":
            license_counts["restricted"] += 1
        elif lr.verdict == "review":
            license_counts["review" if lr.family != "unknown" else "unknown"] += 1
    unused_count = sum(1 for u in usage_results.values() if u.status == "none")
    maintenance_count = sum(
        1 for m in maint_results.values() if m.status in ("concerns", "deprecated")
    )

    health_score = health.compute_health(
        vuln_counts=dict(vuln_severity_counts),
        outdated_counts=dict(outdated_counts),
        license_counts=dict(license_counts),
        unused_count=unused_count,
        maintenance_count=maintenance_count,
        security_checked=security_checked,
        registry_available_ratio=registry_ratio if online else 0.0,
    )
    st.status = "ok"
    st.detail = f"Health score {health_score.score} ({health_score.grade})"
    st.duration_ms = int((time.time() - t0) * 1000)

    # Build per-workspace summary breakdown.
    ws_names_list = sorted(workspace_manifests.keys())
    workspace_summary: Dict[str, Any] = {}
    for ws_name, ws_manifest in workspace_manifests.items():
        ws_direct_names = set(ws_manifest.declared().keys())
        ws_pkg_names = {
            pkg.name for pkg in lockfile.packages.values()
            if workspace_map.get(pkg.id) == ws_name
        }
        ws_outdated = sum(
            1 for n in ws_pkg_names
            if update_results.get(n) and update_results[n].is_outdated
        )
        ws_vulns = sum(
            len(vulns_by_pkg.get(f"{n}@{_version_of(n, lockfile)}", []))
            for n in ws_pkg_names
        )
        workspace_summary[ws_name] = {
            "direct": len(ws_direct_names),
            "total": len(ws_pkg_names),
            "outdated": ws_outdated,
            "vulnerable_packages": ws_vulns,
        }

    # Stage 9 — persist ---------------------------------------------------------
    st = ctx.stage("persist", "Preparing report")
    t0 = time.time()
    for name, info in registry_info.items():
        if info and info.description:
            _DESC_CACHE[name] = info.description
    summary = _build_summary(
        graph, lockfile, vulns_by_pkg, update_results, license_results, usage_results,
        maint_results, vuln_severity_counts,
    )
    summary["workspaces"] = ws_names_list
    summary["workspace_summary"] = workspace_summary

    project_id = db.upsert_project(project_name, str(root), "npm")
    scan_id = _persist(
        db, project_id, ctx, lockfile, graph, vulns_by_pkg, update_results,
        license_results, usage_results, maint_results, health_score, summary,
        workspace_map=workspace_map,
    )
    st.status = "ok"
    st.detail = f"Saved scan #{scan_id}"
    st.duration_ms = int((time.time() - t0) * 1000)

    ctx.warnings.append(f"Workspace scan completed in {int((time.time() - started) * 1000)} ms")
    db.execute(
        "UPDATE scans SET stages_json=?, warnings_json=? WHERE id=?",
        (
            json.dumps([asdict(s) for s in ctx.stages]),
            json.dumps(ctx.warnings),
            scan_id,
        ),
    )
    return scan_id


def _merge_manifest_deps(root_manifest, extra_deps):
    """Return a copy-like manifest with extra_deps merged in."""
    from deplens.parsers.manifest import Manifest

    seen = set(d.name for d in root_manifest.dependencies)
    merged = list(root_manifest.dependencies)
    for dep in extra_deps:
        if dep.name not in seen:
            merged.append(dep)
            seen.add(dep.name)
    return Manifest(
        name=root_manifest.name,
        version=root_manifest.version,
        private=root_manifest.private,
        dependencies=merged,
        scripts=root_manifest.scripts,
        raw=root_manifest.raw,
        warnings=root_manifest.warnings,
    )

