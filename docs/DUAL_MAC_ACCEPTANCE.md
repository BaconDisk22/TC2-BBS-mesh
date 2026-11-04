# Dual Mac build acceptance checklist

The Sitdown BBS supports two **separately packaged** macOS applications from one source tree:

| Download | Architecture | Target |
| --- | --- | --- |
| Intel Mac DMG | x86_64 | Intel Macs |
| Apple Silicon Mac DMG | arm64 | M-series Macs |

These are unsigned test builds until release gates are met. A passing GitHub Actions job is not evidence of physical radio compatibility.

## Automated build gates (each architecture independently)

- [ ] Unit tests and desktop UI tests pass.
- [ ] PyInstaller creates a launchable `.app` and a `.dmg`.
- [ ] `lipo -archs` reports the expected architecture for the bundled executable.
- [ ] The packaged `--sitdown-smoke-test` exits successfully and reports the expected version.
- [ ] `Info.plist` contains the expected version.
- [ ] SHA256SUMS matches the downloaded DMG.
- [ ] The other architecture's workflow remains green after the change.

## Physical Mac acceptance (run separately on Intel and Apple Silicon)

- [ ] DMG mounts and app launches with Gatekeeper approval where needed; do not disable Gatekeeper globally.
- [ ] Demo mode launches without radio or transmissions.
- [ ] Settings survive quit and relaunch.
- [ ] Wi-Fi/TCP radio connection and disconnect/reconnect are observed using authorized equipment.
- [ ] Node list, BBS menus, chat, backups, and logs work as expected.
- [ ] Radio transmission remains opt-in and demo mode never transmits.
- [ ] Record OS version, architecture, app version, workflow URL and actual test results.

## Release policy

Do not replace the Intel build with an ARM build or label either architecture as universal. Prefer two architecture-labeled DMGs under one versioned release. Do not publish a stable release until both architecture-specific acceptance records and security review are complete. Keep Apple Silicon builds as test artifacts until validated on a physical M-series Mac.
