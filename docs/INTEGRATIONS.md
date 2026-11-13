# Integrations: webhooks, assistant link and sensor history

The Sitdown BBS can share what its radio hears with other software. There are
three parts, and **all of them are off until you turn them on** in the
`[integrations]` section of `config.ini`.

In the Mac app, `config.ini` lives in `~/.sitdown-bbs/` (from source, next to
`server.py`). Stop and start the server after changing it. Files the
integrations create (token, line-protocol file) go in an `integrations/`
folder beside `config.ini`.

These integrations cover packets heard by the **BBS radio**. MQTT Wire traffic
is not included.

| Part | What it does | Setting |
| --- | --- | --- |
| Webhooks | POSTs a JSON document to your URLs when a message, node or telemetry packet is heard | `webhooks_enabled` |
| Assistant link | Lets an AI assistant (for example Claude) read nodes, messages and telemetry | `local_api_enabled` |
| Sensor history | Prometheus `/metrics`, InfluxDB line protocol and a Grafana dashboard | `local_api_enabled`, `influx_file`, `influx_url` |

Nothing here listens on your network: the local web server binds to
`127.0.0.1` only and refuses requests addressed to any other host name.

## 1. Webhooks

```ini
[integrations]
webhooks_enabled = True
webhook_urls = https://example.org/hooks/mesh, http://127.0.0.1:1880/mesh
webhook_events = message,node,telemetry
webhook_include_text = True
webhook_secret = choose-a-long-random-string
```

Each event is one `POST` with `Content-Type: application/json`:

```json
{"app": "sitdown-bbs", "event": "message",
 "data": {"node_id": "!1234abcd", "to": "!ffffffff", "direct": false, "channel": 0,
          "text": "hello mesh", "snr": 6.5, "rssi": -88, "time": 1700000000}}
```

* `node` events fire for every packet heard (name, SNR/RSSI, hops, position when known).
* `telemetry` events carry `data.metrics` such as `battery_percent`,
  `voltage_volts`, `temperature_celsius`, `relative_humidity_percent`,
  `barometric_pressure_hpa`, `channel_utilization_percent`.
* Set `webhook_include_text = False` to send message events without the text.
* With `webhook_secret`, each request has `X-Mesh-Signature: sha256=<hex>`, the
  HMAC-SHA256 of the raw body. `X-Mesh-Event` names the event type.

Delivery runs in the background so a slow endpoint never holds up the radio. A
failed POST is retried once; if more than 500 are waiting, new ones are
dropped. Counts and the last error appear in the assistant's
`get_network_status` result.

## 2. Assistant link (MCP)

The assistant link is a small [Model Context Protocol](https://modelcontextprotocol.io)
server. The assistant starts it; it then asks the running BBS server for data
over `127.0.0.1`.

1. Turn on the local API and restart the server:

   ```ini
   [integrations]
   local_api_enabled = True
   local_api_port = 9480
   ```

   On start the server writes a random access token to
   `integrations/assistant-token` (readable only by you). The assistant link reads it
   from there; you never need to copy it.

2. Add the server to Claude Desktop (**Settings → Developer → Edit Config**,
   which opens `claude_desktop_config.json`):

   ```json
   {
     "mcpServers": {
       "sitdown-bbs": {
         "command": "/Applications/The Sitdown BBS.app/Contents/MacOS/The Sitdown BBS",
         "args": ["--sitdown-assistant-mcp"]
       }
     }
   }
   ```

   Running from source instead of the Mac app:

   ```json
   {
     "mcpServers": {
       "sitdown-bbs": {
         "command": "python3",
         "args": ["/path/to/The-Sitdown-BBS/assistant_mcp.py",
                  "--config", "/path/to/The-Sitdown-BBS/config.ini"]
       }
     }
   }
   ```

   For Claude Code, the same command works with
   `claude mcp add sitdown-bbs -- "/Applications/The Sitdown BBS.app/Contents/MacOS/The Sitdown BBS" --sitdown-assistant-mcp`.

3. Restart Claude Desktop. Ask things like *"Which mesh nodes have low battery?"*
   or *"Summarise the last 20 messages on channel 0."*

Tools the assistant gets:

| Tool | Read-only | What it returns |
| --- | --- | --- |
| `get_network_status` | yes | radio link, nodes known / heard in the last hour, packet counts, integration status |
| `list_nodes` | yes | nodes, most recent first, with names, SNR/RSSI, hops, position, latest telemetry |
| `recent_messages` | yes | recent text messages (optionally one channel) |
| `node_telemetry` | yes | telemetry history for one node (id, short or long name) |
| `send_message` | **no** | only listed when `assistant_send_enabled = True` |

**Sending is off by default.** If you set `assistant_send_enabled = True`, the
assistant can post to a channel (200 characters max, channel 0-7). Posts go
through the same path as desktop chat: the same checks and the same limit of
one message every 2 seconds and 6 per minute, and they show up in the CHAT page.
Direct messages from the assistant are not supported. Claude asks before using
a tool that is not marked read-only, but anything it sends goes out on the air
under the BBS radio's name, so leave this off unless you need it.

The server keeps the last 200 messages and up to 500 telemetry points per node
in memory; restarting the server clears them.

## 3. Sensor history: Prometheus, InfluxDB and Grafana

### Prometheus

With `local_api_enabled = True` (and `metrics_enabled = True`, the default),
`http://127.0.0.1:9480/metrics` serves:

* `mesh_up`, `mesh_radio_connected`, `mesh_nodes_known`, `mesh_nodes_heard_last_hour`
* `mesh_packets_total{portnum=...}`
* per node (`node`, `name` labels): `mesh_node_last_heard_timestamp_seconds`,
  `mesh_node_snr`, `mesh_node_rssi`, `mesh_node_battery_percent`,
  `mesh_node_voltage_volts`, `mesh_node_temperature_celsius`,
  `mesh_node_relative_humidity_percent`, `mesh_node_barometric_pressure_hpa`,
  `mesh_node_channel_utilization_percent`, `mesh_node_air_util_tx_percent`, and more.

`prometheus.yml`:

```yaml
scrape_configs:
  - job_name: mesh
    scrape_interval: 60s
    static_configs:
      - targets: ["127.0.0.1:9480"]
```

### InfluxDB line protocol

Three options, all using measurement `mesh_telemetry`, tags `node` and `name`,
one field per reading, second precision:

* `influx_file = telemetry.lp` appends every telemetry packet to
  `integrations/telemetry.lp` for `influx write --precision s --file integrations/telemetry.lp`.
* `influx_url` + `influx_token` push each line to InfluxDB v2, e.g.
  `http://127.0.0.1:8086/api/v2/write?org=home&bucket=mesh&precision=s`.
* `http://127.0.0.1:9480/influx` returns everything in memory as line protocol
  (handy for Telegraf's `inputs.http` with `data_format = "influx"`).

```
mesh_telemetry,node=!1234abcd,name=Ridge\ Top battery_percent=87.0,voltage_volts=4.01 1700000002
```

### Grafana

Import [`docs/grafana/mesh-telemetry-dashboard.json`](grafana/mesh-telemetry-dashboard.json)
(**Dashboards → New → Import**) and pick your Prometheus data source. It shows
radio state, nodes heard, packet rate, battery, voltage, temperature, humidity,
pressure, channel utilization and SNR, with a node picker.

## Privacy

* Webhook bodies can contain message text and node positions. Send them only
  to endpoints you control, use `https://` off your own machine, and set
  `webhook_include_text = False` if you only need metadata.
* The local API only answers on `127.0.0.1`. `/metrics`, `/influx` and
  `/healthz` need no token so local Prometheus can scrape them; everything under
  `/api/` needs the token in `integrations/assistant-token`.
* Delete `integrations/assistant-token` to rotate the token; a new one is made on the
  next start.
