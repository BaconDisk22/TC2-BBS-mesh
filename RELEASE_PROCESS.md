# Mac release process

This document describes the existing release workflow; it is not evidence of hardware acceptance.

## Build paths

- **Intel Mac test build**: pushes to `main` produce a workflow artifact for testing.
- **Publish Mac Release** (`publish-intel-mac-release.yml`): version tags matching `v*` or a manual workflow dispatch build the app on an Intel (x86_64) and an Apple Silicon (arm64) runner, test and smoke-test each, package `The-Sitdown-BBS-Intel-Mac-<version>.dmg` and `The-Sitdown-BBS-Apple-Silicon-<version>.dmg`, and publish both with one combined `SHA256SUMS.txt`.
- **Releases repository:** if the repository secret `RELEASES_TOKEN` is set, the same files are also published to `TheLogsAreScreaming/The-Sitdown-BBS-Releases`. Without it, that step prints a notice and the release must be copied there by hand.

The latest published candidate reviewed for this update is **v0.1.0-rc.13**. Do not reuse that tag or overwrite its assets. Always select a new version for a new build.

## Publish a new candidate

1. Review and merge intended PRs; confirm the required checks and Intel Mac test-build workflow pass for the exact `main` commit being released.
2. Run `python -m unittest discover -s tests` in a clean environment and record the result.
3. Open **Actions → Publish Mac Release → Run workflow**, choose `main`, and enter a **new, unused** version tag.
4. Alternatively, push a new annotated `v*` tag pointing to the reviewed commit. Do not use both triggers for the same version.
5. Confirm the workflow succeeded and the release has both DMGs and `SHA256SUMS.txt` (in the source repo, and in the Releases repo when `RELEASES_TOKEN` is set).
6. Download both files into the same directory and run `shasum -a 256 -c SHA256SUMS.txt`. Record the workflow URL, commit SHA, version and checksum.
7. Install and launch the DMG on a **physical Intel Mac**. Test Demo mode, Settings, exit/relaunch, **The Books** backup and read-only **Verify Backup** on a test archive.
8. Record real authorized radio testing separately: TCP connection and reconnect, node observations, message delivery and chat relay outcomes. If USB testing is required for the target release, arrange an additional tester; TCP evidence must not be presented as USB evidence.
9. Check logs, screenshots, exported backups and assets for credentials, PSKs and personal data before publication.

## Release safety and limitations

- Current development builds are **unsigned and unnotarized**. Gatekeeper may require user approval in **System Settings → Privacy & Security**. Never disable Gatekeeper globally.
- The publishing workflow requires `GITHUB_TOKEN` with `contents: write`. Publishing to the Releases repo needs `RELEASES_TOKEN`: a fine-grained personal access token limited to `TheLogsAreScreaming/The-Sitdown-BBS-Releases` with **Contents: Read and write**, saved under Settings → Secrets and variables → Actions.
- Automated checks and successful DMG builds **do not prove physical radio behavior** or a macOS Tahoe launch.
- **Verify a backup** only validates an archive. **Restore from backup** replaces app files after saving a safety copy; test it on a spare account before relying on it.
- Radio reachability, SDK connection, observed traffic and confirmed end-to-end delivery are distinct states. Do not claim a delivery test passed based solely on MQTT activity or an open TCP port.
- MQTT monitors never bridge or forward. Operator MQTT sending is off by default (per-network `allow_publish`) and goes only through `sitdown_mqtt_sender.py`.

## Stable v1.0 gate

Follow [the v1.0 release runbook](docs/V1_RELEASE_RUNBOOK.md) and [CentralNJ operator checklist](docs/CENTRALNJ_OPERATOR_CHECKLIST.md). Stable release requires reviewed CI, physical Intel Mac verification, authorized end-to-end radio tests, credential review and explicit human sign-off. If any gate is missing, publish a clearly labeled prerelease instead.
