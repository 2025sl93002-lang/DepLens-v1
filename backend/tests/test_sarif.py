"""Tests for SARIF export and the --fail-on severity gate (issue #15)."""

import json

import pytest

from deplens.__main__ import _fail_on_exit
from deplens.report import render_sarif
from deplens.scanner import run_scan


def _data_with_vulns():
    return {
        "scan": {"lockfile_format": "v3"},
        "vulnerabilities": [
            {
                "package": "lodash", "installed_version": "4.17.11",
                "osv_id": "GHSA-x", "cve": "CVE-2020-0001", "severity": "high",
                "summary": "Prototype pollution", "url": "https://osv.dev/x",
                "fixed_version": "4.17.19",
            },
            {
                "package": "minimist", "installed_version": "1.2.0",
                "osv_id": "GHSA-y", "cve": None, "severity": "moderate",
                "summary": "ReDoS", "url": None, "fixed_version": None,
            },
        ],
    }


def test_sarif_structure():
    doc = json.loads(render_sarif(_data_with_vulns()))
    assert doc["version"] == "2.1.0"
    run = doc["runs"][0]
    assert run["tool"]["driver"]["name"] == "DepLens"
    assert len(run["tool"]["driver"]["rules"]) == 2
    assert len(run["results"]) == 2

    first = run["results"][0]
    assert first["ruleId"] == "CVE-2020-0001"  # CVE preferred over OSV id
    assert first["level"] == "error"  # high -> error
    assert first["locations"][0]["physicalLocation"]["region"]["startLine"] == 1
    assert run["results"][1]["ruleId"] == "GHSA-y"  # falls back to osv_id
    assert run["results"][1]["level"] == "warning"  # moderate -> warning


def test_sarif_empty_is_valid():
    doc = json.loads(render_sarif({"scan": {}, "vulnerabilities": []}))
    run = doc["runs"][0]
    assert run["results"] == []
    assert run["tool"]["driver"]["rules"] == []


async def test_fail_on_gate(db, demo_project):
    scan_id = await run_scan(db, str(demo_project), online=False)
    db.execute(
        "INSERT INTO vulnerabilities(scan_id, package, installed_version, osv_id, severity) "
        "VALUES (?,?,?,?,?)",
        (scan_id, "pkg", "1.0.0", "OSV-TEST", "high"),
    )
    # high meets the 'moderate' threshold -> exit 1
    with pytest.raises(SystemExit) as exc:
        _fail_on_exit(db, scan_id, "moderate")
    assert exc.value.code == 1
    # high is below 'critical' -> no exit
    _fail_on_exit(db, scan_id, "critical")
    # 'none' never fails
    _fail_on_exit(db, scan_id, "none")


async def test_fail_on_clean_scan_passes(db, demo_project):
    scan_id = await run_scan(db, str(demo_project), online=False)
    # No vulnerabilities inserted -> even 'low' must not fail.
    _fail_on_exit(db, scan_id, "low")
