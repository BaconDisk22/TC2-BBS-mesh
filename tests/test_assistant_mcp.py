"""Assistant link (MCP over stdio) against a real localhost API, no radio."""
import io
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

MODULES = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULES))
import assistant_mcp  # noqa: E402
import mesh_integrations as mi  # noqa: E402

PACKETS = [
    {"from": 0xA1B2C3D4, "rxTime": 1700000000,
     "decoded": {"portnum": "NODEINFO_APP", "user": {"longName": "Base Camp", "shortName": "BC"}}},
    {"from": 0xA1B2C3D4, "to": 0xFFFFFFFF, "channel": 0, "rxTime": 1700000001,
     "decoded": {"portnum": "TEXT_MESSAGE_APP", "text": "anyone out there?"}},
    {"from": 0xA1B2C3D4, "rxTime": 1700000002,
     "decoded": {"portnum": "TELEMETRY_APP", "telemetry": {"deviceMetrics": {"batteryLevel": 55}}}},
]


def rpc(server, method, params=None, msg_id=1):
    message = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        message["params"] = params
    return server.handle(message)


class McpProtocolTests(unittest.TestCase):
    def setUp(self):
        self.data_dir = tempfile.mkdtemp()
        self.hub = mi.IntegrationHub(mi.IntegrationSettings(local_api_enabled=True), "test-app", self.data_dir)
        for packet in PACKETS:
            self.hub.observe(packet)
        self.api = mi.LocalApiServer(self.hub, 0, self.data_dir)
        self.api.start()
        self.server = assistant_mcp.McpServer(lambda: assistant_mcp.LocalApiClient(self.api.port, self.api.token))

    def tearDown(self):
        self.api.stop()

    def call(self, name, **args):
        reply = rpc(self.server, "tools/call", {"name": name, "arguments": args})
        return reply["result"]

    def test_initialize_negotiates_version(self):
        result = rpc(self.server, "initialize", {"protocolVersion": "2024-11-05"})["result"]
        self.assertEqual(result["protocolVersion"], "2024-11-05")
        self.assertIn("tools", result["capabilities"])
        unknown = rpc(self.server, "initialize", {"protocolVersion": "1999-01-01"})["result"]
        self.assertEqual(unknown["protocolVersion"], assistant_mcp.PROTOCOL_VERSIONS[0])

    def test_notifications_get_no_reply(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_read_only_tools_listed_send_hidden_by_default(self):
        names = [t["name"] for t in rpc(self.server, "tools/list")["result"]["tools"]]
        self.assertEqual(names, ["get_network_status", "list_nodes", "recent_messages", "node_telemetry"])

    def test_send_tool_appears_only_when_enabled(self):
        self.hub.settings.assistant_send_enabled = True
        names = [t["name"] for t in rpc(self.server, "tools/list")["result"]["tools"]]
        self.assertIn("send_message", names)

    def test_tools_return_mesh_data(self):
        nodes = json.loads(self.call("list_nodes")["content"][0]["text"])["nodes"]
        self.assertEqual(nodes[0]["long_name"], "Base Camp")
        msgs = json.loads(self.call("recent_messages", limit=5)["content"][0]["text"])["messages"]
        self.assertEqual(msgs[0]["text"], "anyone out there?")
        tel = json.loads(self.call("node_telemetry", node="BC")["content"][0]["text"])
        self.assertEqual(tel["points"][0]["battery_percent"], 55)
        status = json.loads(self.call("get_network_status")["content"][0]["text"])
        self.assertEqual(status["nodes_known"], 1)

    def test_send_refused_when_off_even_if_called_directly(self):
        result = self.call("send_message", text="hello")
        self.assertTrue(result["isError"])
        self.assertIn("turned off", result["content"][0]["text"])

    def test_send_when_enabled(self):
        self.hub.settings.assistant_send_enabled = True
        sent = []
        self.hub.send_text = lambda text, ch, dest: sent.append((text, ch, dest))
        result = self.call("send_message", text="hello", channel=2)
        self.assertFalse(result["isError"])
        self.assertEqual(sent, [("hello", 2, None)])

    def test_send_tool_has_no_direct_message_option(self):
        self.assertNotIn("destination", assistant_mcp.SEND_TOOL["inputSchema"]["properties"])

    def test_unknown_tool_and_method(self):
        self.assertEqual(rpc(self.server, "tools/call", {"name": "format_disk"})["error"]["code"], -32602)
        self.assertEqual(rpc(self.server, "bogus/method")["error"]["code"], -32601)

    def test_serve_loop_handles_bad_json(self):
        stdin = io.StringIO('not json\n\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n')
        stdout = io.StringIO()
        self.server.serve(stdin, stdout)
        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(lines[0]["error"]["code"], -32700)
        self.assertEqual(lines[1], {"jsonrpc": "2.0", "id": 7, "result": {}})


class McpWhenAppIsOffTests(unittest.TestCase):
    def test_wrong_token_and_app_off_are_tool_errors(self):
        server = assistant_mcp.McpServer(lambda: assistant_mcp.LocalApiClient(1, "nope", timeout=1))
        result = rpc(server, "tools/call", {"name": "list_nodes", "arguments": {}})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("local_api_enabled", result["content"][0]["text"])

    def test_script_runs_over_stdio(self):
        config = pathlib.Path(tempfile.mkdtemp()) / "config.ini"
        config.write_text("[integrations]\nlocal_api_enabled = True\nlocal_api_port = 9\n", encoding="utf-8")
        requests = "\n".join(json.dumps(m) for m in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})) + "\n"
        out = subprocess.run([sys.executable, str(MODULES / "assistant_mcp.py"), "--config", str(config)],
                             input=requests, capture_output=True, text=True, timeout=30, check=True)
        replies = [json.loads(line) for line in out.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(replies[0]["result"]["serverInfo"]["name"], assistant_mcp.SERVER_NAME)


if __name__ == "__main__":
    unittest.main()
