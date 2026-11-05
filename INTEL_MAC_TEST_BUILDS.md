# Intel Mac test builds

Every push to `main`, pull request, or manual workflow dispatch runs **Intel Mac test build** on GitHub's `macos-15-intel` runner.

1. Open **Actions** → **Intel Mac test build**.
2. Select a **successful** run.
3. Under **Artifacts**, download **The-Sitdown-BBS-Intel-Mac-Test**.
4. Unzip the artifact and open `The-Sitdown-BBS-Intel-Test.dmg`.
5. Drag the app to Applications.
6. **First launch:** open it from Finder and wait — macOS scans unsigned downloads the first time, which can take several minutes on an Intel Mac. If macOS says it can't be opened, use System Settings → Privacy & Security → **Open Anyway**. Don't start it from Terminal the first time; it just waits with no message.

The DMG is an **unsigned, unnotarized development test build**, not a stable release. It includes **READ ME FIRST.txt** and **check-mac-launch.sh**, which reports whether macOS has cleared your copy. See [docs/MAC_FIRST_LAUNCH.md](docs/MAC_FIRST_LAUNCH.md). Only approve a build you trust, and do not disable Gatekeeper globally.

The bundled app creates a user-editable radio configuration at `~/.sitdown-bbs/config.ini` on first non-demo launch. Review it before connecting a radio. Demo mode does not require a radio. Radio connectivity and macOS Tahoe compatibility must still be tested on physical hardware.

Build artifacts expire after **14 days**. This workflow does not publish GitHub Releases and does not require an Apple Developer signing certificate.
