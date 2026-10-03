#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools/sv_release_check.py"
SPEC = importlib.util.spec_from_file_location(
    "sv_unifi_release_check_test",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class UniFiReleaseCheckTests(unittest.TestCase):
    def test_permanent_release_inventory_is_complete(self):
        self.assertEqual(
            release.PERMANENT_CHECKS,
            (
                "switch-vision-unifi2mqtt/self-test.py",
                "switch-vision-unifi2mqtt/hardening-test.py",
                "switch-vision-unifi2mqtt/mqtt-lifecycle-test.py",
                "switch-vision-unifi2mqtt/activity-test.py",
                "switch-vision-unifi2mqtt/classic-port-probe-test.py",
                "switch-vision-unifi2mqtt/multi-controller-test.py",
                "switch-vision-unifi2mqtt/multi-controller-probe-test.py",
                "switch-vision-unifi2mqtt/optional-profile-test.py",
                "release-contract-test.py",
                "tools/test_sv_release_check.py",
            ),
        )

    def test_release_dependency_is_exactly_pinned(self):
        pins = (
            ROOT / "tools/sv_release_check.requirements.txt"
        ).read_text(encoding="utf-8").splitlines()
        self.assertEqual(pins, ["paho-mqtt==2.1.0"])

    def test_current_version_contract_is_coherent(self):
        version = release.resolve_version(ROOT)
        self.assertEqual(version, "4.0.6")
        release.validate_version_contract(ROOT, version)

    def test_release_check_is_local_and_non_publishing(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("SV_RELEASE_CHECK_PASS", source)
        self.assertIn("PYTHONDONTWRITEBYTECODE", source)
        self.assertIn('f"safe.directory={root}"', source)
        for forbidden in (
            "gh api",
            "gh release",
            "docker push",
            "git push",
            "git fetch",
            "pull_request",
            "workflow_dispatch",
        ):
            self.assertNotIn(forbidden, source)

    def test_dockerfile_contract_requires_digest_pinning(self):
        release.validate_dockerfile_contract(ROOT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
