# Radio test session (rc.14 → 1.0)

One sitting that covers every over-the-air feature not yet verified. It is a **plan**, not evidence: fill in the Result column as you go and keep this file (or a copy) as the record for the [v1.0 runbook](V1_RELEASE_RUNBOOK.md).

**You need:**
- **BBS radio:** TEST UNIT `!934cca70` (RAK11200), at 10.0.0.76 over Wi-Fi and with its USB cable.
- **Second radio:** your EDC `!b2c649cb`, with the Meshtastic phone app.
- **The Mac:** running The Sitdown BBS rc.14 or later.
- **Time:** about 45 minutes.

**Sending rule:** every message below goes out over the radio or the internet. Each one is listed here with its exact text so you can approve it before the session. Don't send anything else, and don't include keys, passwords or personal details.

| # | Exact message text | Sent from | Goes to |
| --- | --- | --- | --- |
| M1 | `Sitdown test 1 - DM from the desk` | App, Chat → ✉ EDC | EDC (direct) |
| M2 | `Sitdown test 2 - reply from EDC` | EDC phone app | BBS radio (direct) |
| M3 | `/bulletins` | EDC phone app | BBS radio (direct) |
| M4 | `Sitdown test 4 - CentralNJ over MQTT` | App, Chat → 🌐 CentralNJ | CentralNJ broker |
| M5 | `Sitdown test 5 - CentralNJ by radio` | EDC phone app, CentralNJ channel | Mesh (broadcast) |
| M6 | `/node EDC` | EDC phone app | BBS radio (direct) |

---

## A. Before you start (no sending)

| Step | Do this | Expect | Result |
| --- | --- | --- | --- |
| A1 | Open the app and wait for **Radio connected** in the status bar | Server pill says running; Dashboard counts nodes | |
| A2 | Settings → CentralNJ: check **Channel keys** starts with your channel name, then **Save changes → Apply now** | No "matches none of your topics" warning; server restarts; CentralNJ reconnects | |
| A3 | Chat: turn on **Allow sending this session** | The switch says On | |
| A4 | Nodes → **All** | More nodes than "your radio holds"; CentralNJ/SJMesh sightings listed | |

## B. Direct messages (M1–M3)

| Step | Do this | Expect | Result |
| --- | --- | --- | --- |
| B1 | Chat → **New direct message** → pick EDC → send **M1** and confirm the prompt | Tab ✉ EDC shows the message with **… sending**, then **✓ relayed** or **✓✓ delivered** | |
| B2 | On the EDC phone: is M1 there as a direct message? | Yes, from TEST UNIT | |
| B3 | From the EDC phone, reply **M2** within 10 minutes | M2 appears in ✉ EDC; the BBS does **not** send its menu back | |
| B4 | From the EDC phone, send **M3** | The BBS answers with its bulletin list (slash commands still reach the BBS) | |
| B5 | Press **End conversation**, then repeat **M2** from EDC | The BBS menu answers this time (conversation over) | |

## C. MQTT reading and sending (M4–M5)

| Step | Do this | Expect | Result |
| --- | --- | --- | --- |
| C1 | Leave Chat → 🌐 CentralNJ open for a few minutes | Other people's CentralNJ text appears (if anyone talks) | |
| C2 | From the EDC phone, send **M5** on the CentralNJ channel | Shows in the radio's CentralNJ tab; if your radio uplinks CentralNJ, it also shows in 🌐 CentralNJ | |
| C3 | Settings → CentralNJ: **Allow sending to CentralNJ from Chat** is On; send **M4** from 🌐 CentralNJ and confirm | **… sending**, then **✓ on broker** within a minute. If the broker refuses, the log says why | |
| C4 | Does M4 arrive on the EDC phone (CentralNJ channel)? | Only if a gateway downlinks CentralNJ. Note yes or no; either answer is useful | |

## D. Saved node list over the radio (M6)

| Step | Do this | Expect | Result |
| --- | --- | --- | --- |
| D1 | From the EDC phone, send **M6** | The reply names EDC; nodes the radio forgot show "(saved list)" | |

## E. USB instead of Wi-Fi (no sending)

| Step | Do this | Expect | Result |
| --- | --- | --- | --- |
| E1 | **Stop server**. Plug TEST UNIT in by USB. Dashboard → **USB cable** → **Find USB Ports** | One port is found and filled in | |
| E2 | **Check Radio Connection**, then **Start** | Radio connected; nodes load | |
| E3 | Unplug the USB cable for 10 s, then plug it back in | "Can't reach radio" or a disconnect, then recovery (with Restart after a crash on) | |
| E4 | Switch back to **Network (Wi-Fi)**, 10.0.0.76, Start | Connected again | |

## F. App checks (no sending)

| Step | Do this | Expect | Result |
| --- | --- | --- | --- |
| F1 | Settings → General → Theme **Light**, then **Dark** | Whole window switches; page and log kept | |
| F2 | Text size **Larger**, then **Default** | Everything resizes; nothing cut off | |
| F3 | Settings → General → **Back up now…**, then **Verify a backup…** on that file | Verified | |
| F4 | Stop server → **Restore from backup…** with that file | Lists files, saves a safety copy, restores; Start works afterwards | |
| F5 | Tab through the Dashboard, press Space on a button; try ⌘1–⌘9 and ⌘F | Focus ring visible; shortcuts switch pages | |

## G. 24-hour run (no sending)

| Step | Do this | Expect | Result |
| --- | --- | --- | --- |
| G1 | Leave the app running for 24 hours on Wi-Fi with both MQTT networks | Still running; radio connected or recovered on its own | |
| G2 | Afterwards: Settings → General → **Copy diagnostics**, and save the activity log | No crashes; note any "Can't reach radio" periods and recoveries | |

## Sign-off

- **Build:** rc version and commit; Mac model and macOS version; radio firmware version.
- **Credential check:** screenshots, logs and backups shared anywhere contain no keys or passwords.
- **Gaps:** anything that failed or wasn't tested, and whether it blocks 1.0.
- **Decision:** 1.0, or another rc.
