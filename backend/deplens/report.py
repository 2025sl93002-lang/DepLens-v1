"""Report generation: Markdown, JSON and CSV exports from a persisted scan.

Reports are built entirely from stored scan data so they are reproducible and
honest about limitations and data sources.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List
from urllib.parse import quote

from deplens import __version__
from deplens.db import Database


def build_report_data(db: Database, scan_id: int) -> Dict[str, Any]:
    scan = db.query_one("SELECT * FROM scans WHERE id=?", (scan_id,))
    if scan is None:
        raise ValueError(f"Scan #{scan_id} not found")
    project = db.get_project(scan["project_id"])
    summary = json.loads(scan["summary_json"]) if scan["summary_json"] else {}
    packages = [dict(r) for r in db.query(
        "SELECT * FROM packages WHERE scan_id=? ORDER BY is_direct DESC, name", (scan_id,)
    )]
    vulns = [dict(r) for r in db.query(
        "SELECT * FROM vulnerabilities WHERE scan_id=? ORDER BY "
        "CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'moderate' THEN 2 "
        "WHEN 'low' THEN 3 ELSE 4 END, package", (scan_id,)
    )]
    decisions = [dict(r) for r in db.query(
        "SELECT * FROM decisions WHERE project_id=? ORDER BY updated_at DESC",
        (scan["project_id"],)
    )]
    return {
        "generator": f"DepLens {__version__}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": project,
        "scan": {
            "id": scan["id"],
            "created_at": datetime.fromtimestamp(scan["created_at"], timezone.utc).isoformat(),
            "online": bool(scan["online"]),
            "lockfile_format": scan["lockfile_format"],
            "root_version": scan["root_version"],
            "health_score": scan["health_score"],
            "health_grade": scan["health_grade"],
            "health_confidence": scan["health_confidence"],
        },
        "summary": summary,
        "packages": packages,
        "vulnerabilities": vulns,
        "decisions": decisions,
        "data_sources": [
            "npm registry (registry.npmjs.org) — versions, licenses, publish times",
            "OSV.dev — open-source vulnerability advisories",
            "Local static source analysis — usage evidence",
        ],
        "limitations": [
            "Analysis reflects the lockfile and registry data at scan time.",
            "Usage analysis cannot prove a package is unused (dynamic/plugin loading).",
            "The health score is a prioritisation aid, not a validated risk probability.",
            "Security status is UNKNOWN (not secure) when advisories were not checked.",
            "License classification is heuristic and is not legal advice.",
        ],
    }


def render_json(data: Dict[str, Any]) -> str:
    return json.dumps(data, indent=2, default=str)


def render_csv(data: Dict[str, Any]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "name", "version", "latest", "type", "scope", "depth", "dependents",
        "license", "license_verdict", "update_type", "usage_status",
        "maintenance_status", "max_severity", "vuln_count",
    ])
    for p in data["packages"]:
        writer.writerow([
            p["name"], p["version"], p.get("latest") or "",
            "direct" if p["is_direct"] else "transitive", p["scope"], p.get("depth"),
            p.get("dependents"), p.get("license") or "", p.get("license_verdict") or "",
            p.get("update_type") or "", p.get("usage_status") or "",
            p.get("maintenance_status") or "", p.get("max_severity") or "", p.get("vuln_count", 0),
        ])
    return buf.getvalue()


def render_markdown(data: Dict[str, Any]) -> str:
    s = data["summary"]
    scan = data["scan"]
    project = data["project"] or {}
    health = s.get("health", {})
    lines: List[str] = []
    lines.append(f"# DepLens Report — {project.get('name', 'Unknown project')}")
    lines.append("")
    lines.append(f"- **Generated:** {data['generated_at']}")
    lines.append(f"- **Generator:** {data['generator']}")
    lines.append(f"- **Project path:** `{project.get('path', '')}`")
    lines.append(f"- **Ecosystem:** {project.get('ecosystem', 'npm')}")
    lines.append(f"- **Scan date:** {scan['created_at']}")
    lines.append(f"- **Lockfile format:** {scan['lockfile_format']}")
    lines.append(f"- **Online analysis:** {'yes' if scan['online'] else 'no (offline)'}")
    lines.append("")
    lines.append("## Health score")
    lines.append("")
    lines.append(
        f"**{scan['health_score']}/100 (grade {scan['health_grade']})** — "
        f"confidence: {scan['health_confidence']}"
    )
    lines.append("")
    if health.get("factors"):
        lines.append("Contributing factors:")
        lines.append("")
        for f in health["factors"]:
            lines.append(f"- {f['impact']:+.0f}: {f['label']}")
        lines.append("")
    for note in health.get("notes", []):
        lines.append(f"> {note}")
    lines.append("")
    lines.append("## Dependency summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Total dependencies | {s.get('total', 0)} |")
    lines.append(f"| Direct | {s.get('direct', 0)} |")
    lines.append(f"| Transitive | {s.get('transitive', 0)} |")
    lines.append(f"| Outdated | {s.get('outdated', 0)} |")
    lines.append(f"| Vulnerable packages | {s.get('vulnerable_packages', 0)} |")
    lines.append(f"| License warnings | {s.get('license_warnings', 0)} |")
    lines.append(f"| Unknown licenses | {s.get('unknown_licenses', 0)} |")
    lines.append(f"| Potentially unused | {s.get('potentially_unused', 0)} |")
    lines.append(f"| Maintenance concerns | {s.get('maintenance_concerns', 0)} |")
    lines.append("")

    if data["vulnerabilities"]:
        lines.append("## Security findings")
        lines.append("")
        lines.append("| Package | Version | Severity | Advisory | Fixed in | Summary |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for v in data["vulnerabilities"]:
            adv = v.get("cve") or v.get("osv_id")
            lines.append(
                f"| {v['package']} | {v['installed_version']} | {v['severity']} | "
                f"{adv} | {v.get('fixed_version') or '—'} | {(v.get('summary') or '')[:80]} |"
            )
        lines.append("")

    outdated = [p for p in data["packages"] if p.get("update_type") in ("major", "minor", "patch")]
    if outdated:
        lines.append("## Outdated packages")
        lines.append("")
        lines.append("| Package | Installed | Latest | Update |")
        lines.append("| --- | --- | --- | --- |")
        for p in outdated[:200]:
            lines.append(
                f"| {p['name']} | {p['version']} | {p.get('latest') or '?'} | {p['update_type']} |"
            )
        lines.append("")

    if data["decisions"]:
        lines.append("## Decision records")
        lines.append("")
        lines.append("| Package | Action | Decision | Status | Reason |")
        lines.append("| --- | --- | --- | --- | --- |")
        for d in data["decisions"]:
            lines.append(
                f"| {d['package']} | {d['action']} | {d['decision']} | {d['status']} | "
                f"{(d.get('reason') or '')[:60]} |"
            )
        lines.append("")

    lines.append("## Data sources")
    lines.append("")
    for src in data["data_sources"]:
        lines.append(f"- {src}")
    lines.append("")
    lines.append("## Limitations")
    lines.append("")
    for lim in data["limitations"]:
        lines.append(f"- {lim}")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# SBOM exports (CycloneDX 1.4 / SPDX 2.3)
# --------------------------------------------------------------------------- #

# DepLens severities -> CycloneDX rating vocabulary (critical/high/medium/low/…).
_CDX_SEVERITY = {
    "critical": "critical",
    "high": "high",
    "moderate": "medium",
    "low": "low",
    "unknown": "unknown",
}


def _purl(name: str, version: str) -> str:
    """Build a package URL (purl) for an npm component.

    Scoped names keep the ``@scope/name`` structure with the ``@`` percent-encoded
    in the namespace, per the purl spec (e.g. ``pkg:npm/%40babel/core@7.0.0``).
    """
    ver = quote(version or "", safe="")
    if name.startswith("@") and "/" in name:
        scope, _, pkg = name.partition("/")
        return f"pkg:npm/{quote(scope, safe='')}/{quote(pkg, safe='')}@{ver}"
    return f"pkg:npm/{quote(name or '', safe='')}@{ver}"


def _utc_now_z() -> str:
    """UTC timestamp as ``YYYY-MM-DDThh:mm:ssZ`` (the form SPDX/CycloneDX expect)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def render_cyclonedx(data: Dict[str, Any]) -> str:
    """Render a CycloneDX 1.4 JSON SBOM from persisted scan data.

    Components come from the resolved package set; OSV advisories are emitted as
    ``vulnerabilities`` linked to their component via purl. Hashes are omitted —
    integrity digests are not persisted with the scan (a documented limitation).
    """
    project = data["project"] or {}
    components: List[Dict[str, Any]] = []
    seen_refs: set = set()
    for p in data["packages"]:
        purl = _purl(p["name"], p["version"])
        ref = purl
        n = 1
        while ref in seen_refs:  # guard against duplicate name@version rows
            ref = f"{purl}#{n}"
            n += 1
        seen_refs.add(ref)
        component: Dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": p["name"],
            "version": p["version"],
            "purl": purl,
        }
        lic = p.get("license")
        if lic:
            component["licenses"] = [{"license": {"name": lic}}]
        components.append(component)

    vulnerabilities: List[Dict[str, Any]] = []
    for v in data["vulnerabilities"]:
        entry: Dict[str, Any] = {
            "id": v.get("cve") or v["osv_id"],
            "source": {"name": "OSV"},
            "ratings": [
                {"severity": _CDX_SEVERITY.get((v.get("severity") or "unknown").lower(), "unknown")}
            ],
            "affects": [{"ref": _purl(v["package"], v["installed_version"])}],
        }
        if v.get("url"):
            entry["source"]["url"] = v["url"]
        if v.get("summary"):
            entry["description"] = v["summary"]
        vulnerabilities.append(entry)

    metadata_component: Dict[str, Any] = {
        "type": "application",
        "bom-ref": project.get("name") or "root-project",
        "name": project.get("name") or "project",
    }
    if data["scan"].get("root_version"):
        metadata_component["version"] = data["scan"]["root_version"]

    bom: Dict[str, Any] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.4",
        "serialNumber": f"urn:uuid:{uuid.uuid4()}",
        "version": 1,
        "metadata": {
            "timestamp": _utc_now_z(),
            "tools": [{"vendor": "DepLens", "name": "DepLens", "version": __version__}],
            "component": metadata_component,
        },
        "components": components,
    }
    if vulnerabilities:
        bom["vulnerabilities"] = vulnerabilities
    return json.dumps(bom, indent=2)


def render_spdx(data: Dict[str, Any]) -> str:
    """Render an SPDX 2.3 JSON SBOM from persisted scan data.

    Each component becomes an SPDX package with a purl external ref and a
    ``DESCRIBES`` relationship from the document. License strings are passed
    through as ``licenseDeclared``; ``licenseConcluded`` is ``NOASSERTION`` since
    DepLens does not perform a legal license determination.
    """
    project = data["project"] or {}
    doc_name = f"deplens-sbom-{project.get('name') or 'project'}"
    spdx_packages: List[Dict[str, Any]] = []
    relationships: List[Dict[str, Any]] = []
    for i, p in enumerate(data["packages"]):
        spdxid = f"SPDXRef-Package-{i}"
        spdx_packages.append(
            {
                "SPDXID": spdxid,
                "name": p["name"],
                "versionInfo": p["version"],
                "downloadLocation": "NOASSERTION",
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": p.get("license") or "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": _purl(p["name"], p["version"]),
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": "SPDXRef-DOCUMENT",
                "relatedSpdxElement": spdxid,
                "relationshipType": "DESCRIBES",
            }
        )

    doc = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": doc_name,
        "documentNamespace": f"https://deplens.dev/spdx/{uuid.uuid4()}",
        "creationInfo": {
            "created": _utc_now_z(),
            "creators": [f"Tool: DepLens-{__version__}"],
        },
        "packages": spdx_packages,
        "relationships": relationships,
    }
    return json.dumps(doc, indent=2)


# --------------------------------------------------------------------------- #
# SARIF 2.1.0 export (GitHub code-scanning)
# --------------------------------------------------------------------------- #

# DepLens severity -> SARIF result level.
_SARIF_LEVEL = {
    "critical": "error",
    "high": "error",
    "moderate": "warning",
    "low": "note",
    "unknown": "note",
}
# DepLens severity -> GitHub "security-severity" (CVSS-like 0-10) for grouping.
_SARIF_SECURITY_SEVERITY = {
    "critical": "9.5",
    "high": "8.0",
    "moderate": "5.5",
    "low": "2.5",
    "unknown": "0.0",
}


def render_sarif(data: Dict[str, Any]) -> str:
    """Render a SARIF 2.1.0 report of the scan's vulnerabilities.

    GitHub code scanning ingests this (via ``upload-sarif``) and renders findings
    in the Security tab. One rule is emitted per unique advisory and one result
    per vulnerable package instance. Findings are anchored to the lockfile, since
    dependency advisories have no single source line.
    """
    artifact_uri = "package-lock.json"
    rules: List[Dict[str, Any]] = []
    rule_index: Dict[str, int] = {}
    results: List[Dict[str, Any]] = []

    for v in data["vulnerabilities"]:
        adv_id = v.get("cve") or v["osv_id"]
        sev = (v.get("severity") or "unknown").lower()
        if adv_id not in rule_index:
            rule_index[adv_id] = len(rules)
            rules.append(
                {
                    "id": adv_id,
                    "name": "VulnerableDependency",
                    "shortDescription": {"text": (v.get("summary") or adv_id)[:120]},
                    "fullDescription": {"text": v.get("summary") or f"Advisory {adv_id}"},
                    "helpUri": v.get("url") or "https://osv.dev/",
                    "properties": {
                        "security-severity": _SARIF_SECURITY_SEVERITY.get(sev, "0.0"),
                        "tags": ["security", "dependencies"],
                    },
                }
            )
        fixed = f" (fixed in {v['fixed_version']})" if v.get("fixed_version") else ""
        results.append(
            {
                "ruleId": adv_id,
                "ruleIndex": rule_index[adv_id],
                "level": _SARIF_LEVEL.get(sev, "note"),
                "message": {
                    "text": f"{v['package']}@{v['installed_version']}: "
                    f"{v.get('summary') or adv_id}{fixed}"
                },
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": artifact_uri},
                            "region": {"startLine": 1},
                        }
                    }
                ],
                "partialFingerprints": {
                    "depLensVuln": f"{v['package']}@{v['installed_version']}:{adv_id}"
                },
            }
        )

    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "DepLens",
                        "informationUri": "https://github.com/deplens/deplens",
                        "version": __version__,
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(doc, indent=2)
