# THE SITDOWN BBS — v1.0 release runbook

This is a **manual verification plan**, not a claim that any test has passed.

## Before tagging
1. Merge reviewed PRs after GitHub Actions pass on the actual merge commit.
2. Run `python -m unittest discover -s tests` in a clean environment.
3. Build the Intel Mac DMG with the existing GitHub Actions workflow.
4. Install the DMG on an actual Intel Mac and verify launch, Settings, radio preflight and clean exit.
5. Connect an authorized Meshtastic radio over USB; record SDK connection, disconnect and recovery.
6. Repeat over TCP with an exclusive authorized radio session.
7. Configure CentralNJ channel/PSK and MQTT uplink/downlink **on the radio**; never commit secrets.
8. Use two authorized endpoints to send a unique small message in each direction. Record times and independent receipt.
9. MQTT broker message counts alone do **not** prove decoded Meshtastic delivery.
10. Review logs, screenshots, diagnostics and release assets for credentials and PSKs.

## Session checklist
Steps 4–8 are laid out as one sitting, with exact message texts to approve first, in [RADIO_TEST_SESSION.md](RADIO_TEST_SESSION.md). It also covers direct messages, MQTT read/send, USB, restore and the 24-hour run.

## Evidence
CI run URL and commit SHA; Intel Mac OS/architecture and DMG checksum; USB/TCP test notes; both-direction CentralNJ delivery evidence; explicit human sign-off.

## Stop ship
Do not tag stable v1.0 without passing Intel Mac install, two-way radio test, credential review and regression suite. Ship a beta instead and document gaps.
