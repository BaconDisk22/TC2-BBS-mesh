# First launch on macOS (unsigned builds)

The Sitdown BBS test and release DMGs are **unsigned and unnotarized**. macOS
treats a freshly downloaded unsigned app specially the first time it opens.

## What actually happens

1. The browser marks the download (`com.apple.quarantine`, flags such as `0083`).
2. On the **first** launch, macOS scans the whole app bundle before letting it
   start. The bundle holds hundreds of files, so on an Intel Mac this can take
   **several minutes**.
3. When macOS clears it, it sets the `0x40` quarantine flag (e.g. `00c3`, `03c1`)
   and every later launch is immediate.

While that first check runs, the app's own code has **not started**. Launched
from Finder you may see "Verifying…". Launched any other way — especially by
running `Contents/MacOS/The Sitdown BBS` from Terminal — there is no prompt, and
the process simply sits at `_dyld_start` with 0% CPU. That looks exactly like a
hang, and it is what the November 2026 "Intel startup hang" turned out to be:

| Same build (`test-49bdbe7`), Intel Mac, macOS 26.7.2 | Result |
|---|---|
| Copy already cleared by macOS (flags `03c1`) | Smoke test passes; normal launch works |
| Fresh copy marked as an uncleared download (`0083`) | Stuck at `_dyld_start`, 0% CPU |
| That same copy after macOS set `0x40` (`00c3`) | Smoke test passes |

No PyInstaller, bootloader, or dependency change can affect this: it happens
before any bundled code runs. Developer ID signing and notarization is the
permanent fix.

## What to tell users

Every DMG includes **READ ME FIRST.txt** and **check-mac-launch.sh**:

- Open the app from **Finder** the first time and give it a few minutes.
- If macOS says it can't be opened: **System Settings → Privacy & Security →
  Open Anyway**.
- Don't start the program file from Terminal the first time.
- Never disable Gatekeeper.

## Checking a copy

```bash
bash scripts/check-mac-launch.sh "/Applications/The Sitdown BBS.app"
```

It is read-only and reports one of:

| Exit | Verdict | Meaning |
|---|---|---|
| 0 | READY | not a quarantined download, or macOS already cleared it |
| 1 | FIRST-LAUNCH CHECK PENDING | quarantined download without the `0x40` flag |
| 2 | DAMAGED | `codesign --verify --deep --strict` fails; re-download |
| 3 | WRONG MAC TYPE | Intel build on Apple Silicon or the reverse |
| 4 | NOT FOUND | no app at that path |

CI runs the checker against every freshly built app (ready), a quarantined copy
(pending), an altered copy (damaged) and a missing path, and confirms both help
files are inside the finished DMG.

## Collecting evidence if a launch really hangs

1. `bash check-mac-launch.sh` — if it says PENDING, it is the first-launch check.
2. If it says READY and the app still doesn't open:
   `ps -axo pid,stat,%cpu,etime,command | grep -i sitdown` and
   `sample <pid> 3 -file sitdown.sample` — a stack in Python or Tk (not
   `_dyld_start`) is a real app problem worth a bug report.
