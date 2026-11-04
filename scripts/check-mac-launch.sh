#!/bin/bash
# Report why The Sitdown BBS might not open on this Mac. Read-only: changes nothing.
#   usage: check-mac-launch.sh [path/to/The Sitdown BBS.app]
# Exit codes: 0 ready, 1 first-launch check pending, 2 damaged, 3 wrong Mac type, 4 not found.
set -u
APP=${1:-"/Applications/The Sitdown BBS.app"}
BIN="$APP/Contents/MacOS/The Sitdown BBS"
say() { printf '%s\n' "$*"; }

if [ ! -x "$BIN" ]; then
  say "NOT FOUND: no app at $APP"
  say "Next: drag The Sitdown BBS from the DMG into Applications, or pass the app's path."
  exit 4
fi

version=$(/usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' "$APP/Contents/Info.plist" 2>/dev/null || echo unknown)
app_arch=$(lipo -archs "$BIN" 2>/dev/null || echo unknown)
mac_arch=$(uname -m)
if [ "$(sysctl -n sysctl.proc_translated 2>/dev/null)" = 1 ]; then mac_arch=arm64; fi
say "App:     $APP"
say "Version: $version"
say "Built for: $app_arch    This Mac: $mac_arch"

case " $app_arch " in
  *" $mac_arch "*) ;;
  *" x86_64 "*)
    if [ "$mac_arch" = arm64 ]; then
      say "WRONG MAC TYPE: this is the Intel build. Download the Apple Silicon DMG for this Mac."
      exit 3
    fi ;;
  *)
    say "WRONG MAC TYPE: this build is for $app_arch; this Mac is $mac_arch. Download the matching DMG."
    exit 3 ;;
esac

if ! codesign --verify --deep --strict "$APP" >/dev/null 2>&1; then
  say "DAMAGED: the app's contents don't match its signature (incomplete download or edited files)."
  say "Next: delete this copy and download the DMG again."
  exit 2
fi
say "Contents: intact (ad-hoc signature verifies)"

quarantine=$(xattr -p com.apple.quarantine "$APP" 2>/dev/null || true)
if [ -z "$quarantine" ]; then
  say "READY: not marked as a download; macOS will open it directly."
  exit 0
fi
flags=$((16#${quarantine%%;*}))
if (( flags & 0x40 )); then
  say "READY: macOS has already cleared this copy (quarantine flags ${quarantine%%;*})."
  exit 0
fi
say "FIRST-LAUNCH CHECK PENDING: macOS hasn't cleared this downloaded copy yet (flags ${quarantine%%;*})."
say "Next: open it from Finder (Applications → double-click) and wait; the first check can take"
say "several minutes on an Intel Mac. If macOS says it can't be opened: System Settings →"
say "Privacy & Security → Open Anyway. Don't start it from Terminal the first time — it will just wait."
exit 1
