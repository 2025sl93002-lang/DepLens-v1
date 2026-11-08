"""Tests for the weekly-downloads popularity signal (issue #11).

Offline and deterministic — no network. Registry data is simulated by
constructing ``RegistryInfo`` objects directly and passing them to the
analyzer, matching the project's established "exercise online analysis via
crafted inputs, not live services" testing approach.
"""

from deplens.analyzers.maintenance import analyze_maintenance
from deplens.services.registry import RegistryInfo


def _info(**kwargs) -> RegistryInfo:
    base = dict(name="demo", available=True, version_times={"1.0.0": "2024-01-01T00:00:00.000Z"})
    base.update(kwargs)
    return RegistryInfo(**base)


def test_maintenance_surfaces_weekly_downloads():
    result = analyze_maintenance("demo", _info(weekly_downloads=1234567))
    assert result.weekly_downloads == 1234567
    assert any("1,234,567 downloads last week" in s for s in result.signals)


def test_maintenance_downloads_absent_when_none():
    result = analyze_maintenance("demo", _info(weekly_downloads=None))
    assert result.weekly_downloads is None
    assert not any("downloads last week" in s for s in result.signals)


def test_maintenance_downloads_none_when_offline():
    # No registry data at all (offline scan or package not found).
    result = analyze_maintenance("demo", None)
    assert result.weekly_downloads is None
    assert not any("downloads last week" in s for s in result.signals)
