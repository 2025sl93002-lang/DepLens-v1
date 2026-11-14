"""Tests for the CycloneDX 1.4 / SPDX 2.3 SBOM exports (issue #17).

Offline and deterministic: the demo fixture is scanned with ``online=False`` so
packages come from the lockfile without any registry/OSV network calls.
"""

import json

from deplens.report import _purl, build_report_data, render_cyclonedx, render_spdx
from deplens.scanner import run_scan


async def test_cyclonedx_structure(db, demo_project):
    scan_id = await run_scan(db, str(demo_project), online=False)
    bom = json.loads(render_cyclonedx(build_report_data(db, scan_id)))

    assert bom["bomFormat"] == "CycloneDX"
    assert bom["specVersion"] == "1.4"
    assert bom["serialNumber"].startswith("urn:uuid:")
    assert bom["metadata"]["tools"][0]["name"] == "DepLens"

    components = bom["components"]
    assert len(components) > 0
    for comp in components:
        assert comp["type"] == "library"
        assert comp["name"] and comp["version"]
        assert comp["purl"].startswith("pkg:npm/")
    refs = [c["bom-ref"] for c in components]
    assert len(refs) == len(set(refs)), "bom-ref values must be unique"


async def test_spdx_structure(db, demo_project):
    scan_id = await run_scan(db, str(demo_project), online=False)
    doc = json.loads(render_spdx(build_report_data(db, scan_id)))

    assert doc["spdxVersion"] == "SPDX-2.3"
    assert doc["dataLicense"] == "CC0-1.0"
    assert doc["SPDXID"] == "SPDXRef-DOCUMENT"
    assert doc["creationInfo"]["creators"][0].startswith("Tool: DepLens-")

    packages = doc["packages"]
    assert len(packages) > 0
    for p in packages:
        assert p["downloadLocation"] == "NOASSERTION"
        assert p["licenseConcluded"] == "NOASSERTION"
        assert p["externalRefs"][0]["referenceLocator"].startswith("pkg:npm/")

    described = {
        r["relatedSpdxElement"] for r in doc["relationships"]
        if r["relationshipType"] == "DESCRIBES"
    }
    assert described == {p["SPDXID"] for p in packages}


async def test_component_count_matches_packages(db, demo_project):
    scan_id = await run_scan(db, str(demo_project), online=False)
    data = build_report_data(db, scan_id)
    cdx = json.loads(render_cyclonedx(data))
    spdx = json.loads(render_spdx(data))
    assert len(cdx["components"]) == len(data["packages"])
    assert len(spdx["packages"]) == len(data["packages"])


def test_purl_encoding():
    assert _purl("lodash", "4.17.21") == "pkg:npm/lodash@4.17.21"
    assert _purl("@babel/core", "7.0.0") == "pkg:npm/%40babel/core@7.0.0"
