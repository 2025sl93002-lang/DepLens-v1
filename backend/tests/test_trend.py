"""Tests for the health-score trend endpoint and project sparkline data (issue #18).

Offline and deterministic: the demo fixture is scanned with ``online=False``.
"""

from fastapi.testclient import TestClient

from deplens.main import app
from deplens.scanner import run_scan


async def test_trend_endpoint_shape(db, demo_project):
    scan_id = await run_scan(db, str(demo_project), online=False)
    project_id = db.query_one("SELECT project_id FROM scans WHERE id=?", (scan_id,))["project_id"]

    client = TestClient(app)
    res = client.get(f"/api/projects/{project_id}/trend")
    assert res.status_code == 200
    trend = res.json()["trend"]
    assert len(trend) == 1  # one scan so far
    point = trend[0]
    assert point["scan_id"] == scan_id
    assert set(point) == {
        "scan_id", "created_at", "health_score", "health_grade",
        "vulnerable_packages", "outdated", "total",
    }
    assert point["health_score"] is not None


async def test_trend_is_chronological(db, demo_project):
    for _ in range(3):
        await run_scan(db, str(demo_project), online=False)
    project_id = db.query_one("SELECT id FROM projects LIMIT 1")["id"]

    client = TestClient(app)
    trend = client.get(f"/api/projects/{project_id}/trend").json()["trend"]
    assert len(trend) == 3
    created = [p["created_at"] for p in trend]
    assert created == sorted(created), "trend points must be oldest-first"


def test_trend_unknown_project_404(db):
    client = TestClient(app)
    assert client.get("/api/projects/999999/trend").status_code == 404


async def test_recent_scores_in_project_list(db, demo_project):
    for _ in range(2):
        await run_scan(db, str(demo_project), online=False)
    projects = db.get_projects()
    assert len(projects) == 1
    scores = projects[0]["recent_scores"]
    assert isinstance(scores, list) and len(scores) == 2
    assert all(isinstance(s, int) for s in scores)
