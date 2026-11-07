# Two-way MQTT design gate for THE SITDOWN BBS

**Current implementation:**
- **Receive:** the app decrypts only channels the operator gave a key for (`channel_keys`). Their broadcast text appears in CHAT; everything else stays metadata only.
- **Send:** the operator can publish typed channel text through `sitdown_mqtt_sender.py`, the only publishing path. It is gated by `allow_publish` (off by default), a per-channel confirmation each session, a channel key, a subscribed `+` topic, a connected broker and a rate limit.
- **Never:** the app does not bridge or forward packets between radio and MQTT, or between networks.

## Protocol compatibility
- Confirm the authorized CentralNJ and SJMesh MQTT root topics, broker authentication, TLS, and channel policies with network operators.
- Identify the actual Meshtastic protobuf envelope and packet encoding in use. Do not treat raw broker payloads as plaintext messages.
- Respect channel keys, sender identity, packet IDs, hop limits, routing semantics and firmware compatibility.
- Reject malformed packets and unknown ports; do not attempt to decrypt without explicitly configured keys.

## Send authorization
- Per-network transmission permission defaults **off**.
- Require explicit operator confirmation before first send in each session.
- Demo mode and read-only monitoring never publish.
- Enforce message size, rate limits, per-network queue bounds and delivery error reporting.
- Avoid transmitting credentials, raw private channel keys or personal location in logs.

## Loop and cross-network protection
- Never bridge CentralNJ and SJMesh automatically.
- Use bounded duplicate detection keyed by source, packet ID, channel and destination.
- Prevent radio-to-MQTT-to-radio echoes.
- Define TTL and replay rejection before enabling any outbound path.

## Required tests
1. Offline protobuf decode/encode round-trip using permitted test vectors.
2. Auth failure, malformed packet, unknown channel and missing key behavior.
3. Duplicate packets, echo loops, reconnect/retry storms and queue overflow.
4. Explicit per-network consent, independent disable switches, demo mode safety.
5. Real broker interoperability in a controlled environment with operator permission.
6. Real Intel Mac + Meshtastic radio send/receive tests with observable ACK semantics.

Do not label MQTT receive-only activity as delivered radio messages. Keep outbound features behind separate reviewed PRs and manual acceptance gates.
