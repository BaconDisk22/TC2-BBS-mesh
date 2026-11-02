# CentralNJ and SJMesh: Intel Mac verification checklist

These desktop MQTT sessions are **receive-only activity monitors**, not a two-way Meshtastic transport. No broker publish, radio transmission, or cross-network bridge is implemented here.

## Before testing
- Build and launch the current Intel Mac release candidate.
- Save separate broker hostname, port, TLS setting, authorized exact topic and credentials for each network in Settings. Never put credentials in an issue, screenshot, or CI log.
- Leave both profiles disabled unless you are authorized to subscribe.
- Use only the narrowest authorized topics: up to 4 comma-separated entries, each an exact topic or one channel from every gateway (`msh/<region>/2/e/<channel>/+`), e.g. `msh/US/2/e/SJMesh/+, msh/US/2/e/LongFast/+`. No other wildcards.
- Do not enable auto-connect until manual connectivity is verified.

## Manual connection
1. Enable CentralNJ only, save, restart the app, and click **Connect CentralNJ**.
2. Verify the CentralNJ status; SJMesh must remain disconnected.
3. With authorized test traffic on the selected topic, check CentralNJ MQTT RX increments. Radio RX/TX counts must not change as a consequence.
4. Disconnect CentralNJ and verify that it stops.
5. Repeat for SJMesh only.
6. Enable both and connect both; verify their counters and statuses remain independent.
7. Disconnect one; the other must stay connected.
8. Disconnect broker networking temporarily. Verify status and reconnect behavior, without freezes or crashes.
9. Quit the app; both MQTT client loops should stop.

## Auto-connect and safety
- Set enabled=true and auto_connect=true for CentralNJ only. Restart and confirm SJMesh stays offline.
- Repeat for SJMesh only, then both.
- Confirm enabled=false or auto_connect=false prevents automatic startup for that profile.
- Run Demo mode: no broker connection should be opened.
- Try an empty topic, a `#` topic, or a `+` anywhere but the gateway position: connection must be refused.
- Verify credentials never appear in diagnostics or screenshots.

## Release gate
Record the app build SHA, macOS version, broker-side permission, observed status and RX count, and any error messages. Do not claim live broker interoperability based only on offline tests. Two-way MQTT publishing remains out of scope.
