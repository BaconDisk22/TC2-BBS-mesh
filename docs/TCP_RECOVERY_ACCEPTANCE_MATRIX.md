# TCP recovery acceptance matrix (Intel Mac)

This is an executable **manual test plan**, not a claim that tests have passed. It covers authorized Meshtastic TCP radios only. It does not substitute for automated unit tests or two-way delivery evidence.

## Preparation

- Use a physical Intel Mac, the DMG from the PR's exact commit, and an authorized radio reachable over Wi-Fi/TCP (typically port 4403).
- Record macOS version, app build/version, radio firmware, radio address (redact it from public logs), commit SHA and CI run URL.
- Back up any existing BBS data. Use a separate test profile, and confirm that demo mode is explicitly labeled.
- Make sure no other application owns the radio's exclusive TCP session. Do not transmit to public channels during this test.

## Scenarios

| ID | Action | Expected result | Evidence |
| --- | --- | --- | --- |
| TCP-01 | Start with radio reachable; connect and start BBS | SDK session becomes connected; radio node observations appear only when actually received | Time-stamped status and sanitized log |
| TCP-02 | Disconnect Wi-Fi or power off radio during active session | UI distinguishes a lost connection from a quiet but connected radio; no stale 'connected' claim | Status before/after, time to detection |
| TCP-03 | Restore the same radio before retry budget expires | Opt-in recovery reconnects once radio is reachable; no duplicate BBS worker | Attempt count, eventual session status |
| TCP-04 | Leave radio offline through retry limit | Retries stop at configured cap; user gets a clear actionable failure, not endless reconnects | Retry count, final status |
| TCP-05 | Stop BBS manually while recovery countdown is active | Explicit stop cancels recovery; no unexpected automatic restart | Log showing stop and absence of later restart |
| TCP-06 | Rapidly disconnect/reconnect twice | No overlapping TCP sessions, duplicate callbacks, or UI freeze | Attempt and worker counts |
| TCP-07 | Enter demo mode while radio unavailable | Demo remains clearly labeled; no real TCP contact or false delivered-message claim | UI and network observations |
| TCP-08 | Wrong IP or refused TCP port | Preflight reports transport reachability failure, not a protocol or delivery success | Sanitized diagnostic |
| TCP-09 | TCP port accepts connection but SDK handshake fails | Status does not report a working Meshtastic session merely because socket opened | Sanitized SDK error |
| TCP-10 | Restart app after an interrupted session | No phantom connected status or automatically resumed transmission without configured consent | Fresh launch and status |

## Pass criteria

1. Each scenario is marked **PASS**, **FAIL**, or **BLOCKED**; never infer PASS from a missing exception.
2. Record retry settings, elapsed time and whether recovery was enabled. Automatic recovery is opt-in and must honor user stop.
3. Status language distinguishes **TCP reachable**, **SDK connected**, **traffic observed**, **radio relay acknowledged**, and **end-to-end delivered**. These are not interchangeable.
4. Check logs and screenshots for secrets and private node details before attaching them to a PR.
5. For any FAIL, capture reproduction steps, expected/actual result, and the exact commit; open a targeted code-fix PR with a regression test.
6. A hardware-free CI pass does not replace physical Intel Mac or radio verification.

## Result template

- Commit / DMG / SHA256:
- Intel Mac / macOS:
- Radio / firmware / TCP port:
- Recovery settings / retry limit:
- Scenario ID:
- PASS / FAIL / BLOCKED:
- Observed status and elapsed time:
- Sanitized log or screenshot:
- Follow-up issue/PR:

Do not mark this plan complete until a human has actually run the cases. Stable release requirements remain in [V1_RELEASE_RUNBOOK.md](V1_RELEASE_RUNBOOK.md).
