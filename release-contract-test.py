from pathlib import Path

workflow = Path('.github/workflows/publish-release.yml').read_text(encoding='utf-8')

required = [
    'pull_request_target:',
    '- .sv-release-request.json',
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.event.pull_request.base.ref == 'main'",
    'Require transport-only one-file PR',
    'test "${#changed[@]}" -eq 1',
    'test "${changed[0]}" = ".sv-release-request.json"',
    'Require exact current main target',
    'test "$TARGET_SHA" = "$BASE_SHA"',
    'refs/heads/main',
    'refs/tags/$VERSION',
    'Run complete regression suite',
    'python switch-vision-unifi2mqtt/multi-controller-test.py',
    'python switch-vision-unifi2mqtt/multi-controller-probe-test.py',
    'matrix:',
    'arch: [amd64, arm64]',
    'permissions:\n      contents: write',
    'gh release create "$VERSION"',
    '--target "$TARGET_SHA"',
    '--latest',
    'Verify public tag and release target',
    'test "$tag_sha" = "$TARGET_SHA"',
    'gh api "repos/${GITHUB_REPOSITORY}/releases/tags/$VERSION" > /tmp/release.json',
    "Path('/tmp/release.json').read_text(encoding='utf-8')",
    "data.get('draft') is not False",
    "data.get('prerelease') is not False",
    "data.get('target_commitish') != os.environ['TARGET_SHA']",
]

missing = [item for item in required if item not in workflow]
if missing:
    raise SystemExit('release publisher contract missing: ' + ', '.join(repr(item) for item in missing))

for forbidden in [
    'pull_request:\n',
    'workflow_dispatch:',
    'actions/checkout@v',
    "python3 - <<'PY' <<<",
    'release_json="$(gh api',
]:
    if forbidden in workflow:
        raise SystemExit(f'forbidden release publisher construct present: {forbidden!r}')

print('UniFi2MQTT release publisher contract: PASS')
