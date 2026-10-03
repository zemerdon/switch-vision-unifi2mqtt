#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
PERMANENT_CHECKS = (
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
)
PYTHON_ROOTS = ("switch-vision-unifi2mqtt", "tools")
GENERATED_ROOTS = ("switch-vision-unifi2mqtt", "tools")


def release_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=release_env(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if proc.stdout:
        print(proc.stdout, end="" if proc.stdout.endswith("\n") else "\n")
    if proc.returncode:
        raise SystemExit(
            f"UniFi2MQTT release check command failed ({proc.returncode}): "
            + " ".join(args)
        )
    return proc.stdout or ""


def git_status(root: Path) -> str:
    proc = subprocess.run(
        [
            "git",
            "-c",
            f"safe.directory={root}",
            "-C",
            str(root),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        raise SystemExit(
            "UniFi2MQTT git status failed: "
            + (proc.stderr or proc.stdout or "").strip()
        )
    return proc.stdout or ""


def resolve_version(root: Path) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not SEMVER_RE.fullmatch(version):
        raise SystemExit(
            f"UniFi2MQTT VERSION is not exact semantic version: {version!r}"
        )
    return version


def validate_version_contract(root: Path, version: str) -> None:
    config = (
        root / "switch-vision-unifi2mqtt/config.yaml"
    ).read_text(encoding="utf-8")
    config_match = re.search(
        r'^version:\s*["\']?(\d+\.\d+\.\d+)["\']?\s*$',
        config,
        flags=re.MULTILINE,
    )
    if config_match is None or config_match.group(1) != version:
        raise SystemExit(
            "UniFi2MQTT config.yaml version does not match VERSION"
        )

    expected_literals = {
        "switch-vision-unifi2mqtt/port_activity.py": f'VERSION = "{version}"',
        "switch-vision-unifi2mqtt/classic_port_probe.py": f'VERSION = "{version}"',
        "switch-vision-unifi2mqtt/run.sh": f'VERSION="{version}"',
    }
    for relative, marker in expected_literals.items():
        text = (root / relative).read_text(encoding="utf-8")
        if marker not in text:
            raise SystemExit(
                f"UniFi2MQTT version contract failed for {relative}: "
                f"missing {marker!r}"
            )

    root_changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    addon_changelog = (
        root / "switch-vision-unifi2mqtt/CHANGELOG.md"
    ).read_text(encoding="utf-8")
    if root_changelog != addon_changelog:
        raise SystemExit(
            "UniFi2MQTT root/add-on changelog copies are not byte-identical"
        )
    if not root_changelog.startswith(f"## v{version}\n"):
        raise SystemExit(
            "UniFi2MQTT changelog does not start with the current version"
        )

    readme = (
        root / "switch-vision-unifi2mqtt/README.md"
    ).read_text(encoding="utf-8")
    if f"## UniFi2MQTT {version}" not in readme:
        raise SystemExit(
            "UniFi2MQTT README current-version heading is missing"
        )

    print(f"UniFi2MQTT {version} version metadata contract: PASS")


def generated_junk(root: Path) -> list[Path]:
    problems: list[Path] = []
    for relative in GENERATED_ROOTS:
        base = root / relative
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir() and path.name == "__pycache__":
                problems.append(path)
            elif path.is_file() and (
                path.suffix in {".pyc", ".pyo"}
                or path.name == ".DS_Store"
            ):
                problems.append(path)
    return problems


def reject_generated_junk(root: Path) -> None:
    problems = generated_junk(root)
    if problems:
        shown = ", ".join(
            str(path.relative_to(root)) for path in problems[:20]
        )
        raise SystemExit(
            "UniFi2MQTT generated cache/junk material present: " + shown
        )
    print("UniFi2MQTT source hygiene: PASS")


def compile_python_sources(root: Path) -> None:
    paths: list[Path] = []
    for relative in PYTHON_ROOTS:
        base = root / relative
        if not base.exists():
            continue
        paths.extend(sorted(base.glob("*.py")))
    paths.append(root / "release-contract-test.py")
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    if not unique:
        raise SystemExit("UniFi2MQTT Python source inventory is empty")
    for path in unique:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
    print(f"UniFi2MQTT Python syntax: PASS ({len(unique)} files)")


def validate_dockerfile_contract(root: Path) -> None:
    text = (
        root / "switch-vision-unifi2mqtt/Dockerfile"
    ).read_text(encoding="utf-8")
    required = (
        "FROM ghcr.io/home-assistant/base:",
        "@sha256:",
        "apk add --no-cache python3 py3-paho-mqtt",
        'CMD [ "/run.sh" ]',
    )
    missing = [token for token in required if token not in text]
    if missing:
        raise SystemExit(
            "UniFi2MQTT Dockerfile release contract missing: "
            + ", ".join(missing)
        )
    print("UniFi2MQTT Dockerfile release contract: PASS")


def run_permanent_checks(root: Path) -> None:
    for relative in PERMANENT_CHECKS:
        path = root / relative
        if not path.is_file():
            raise SystemExit(
                f"UniFi2MQTT permanent release check is missing: {relative}"
            )
        print(f"=== {relative} ===")
        run([sys.executable, str(path)], root)
    print(
        "UniFi2MQTT permanent regression suite: "
        f"PASS ({len(PERMANENT_CHECKS)} checks)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the product-owned Switch Vision UniFi2MQTT "
            "release validation."
        )
    )
    parser.add_argument("--mode", choices=("release",), required=True)
    args = parser.parse_args()
    if args.mode != "release":
        raise SystemExit("unsupported release-check mode")

    root = ROOT
    baseline_status = git_status(root)
    version = resolve_version(root)

    reject_generated_junk(root)
    compile_python_sources(root)
    run(["bash", "-n", "switch-vision-unifi2mqtt/run.sh"], root)
    validate_version_contract(root, version)
    validate_dockerfile_contract(root)
    run_permanent_checks(root)
    reject_generated_junk(root)
    validate_version_contract(root, version)

    final_status = git_status(root)
    if final_status != baseline_status:
        print(
            "UniFi2MQTT release check changed repository state.",
            file=sys.stderr,
        )
        print("--- baseline status ---", file=sys.stderr)
        print(
            baseline_status,
            end="" if baseline_status.endswith("\n") else "\n",
            file=sys.stderr,
        )
        print("--- final status ---", file=sys.stderr)
        print(
            final_status,
            end="" if final_status.endswith("\n") else "\n",
            file=sys.stderr,
        )
        return 1

    print(f"UniFi2MQTT {version} deterministic release validation: PASS")
    print("SV_RELEASE_CHECK_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
