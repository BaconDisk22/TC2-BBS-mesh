"""CI guard for MQTT desktop features actually present in the integration branch."""
import inspect
import unittest
from sitdown_desktop import SitdownApp


class MQTTDesktopFeatureGuard(unittest.TestCase):
    def test_connection_and_startup_hooks_exist(self):
        for name in ('mqtt_network_action', 'start_opted_in_mqtt'):
            self.assertTrue(callable(getattr(SitdownApp, name, None)), name)

    def test_opt_in_gates_and_demo_guard(self):
        source = inspect.getsource(SitdownApp.start_opted_in_mqtt)
        for required in ("'enabled'", "'auto_connect'", 'self.demo.get()'):
            self.assertIn(required, source)

    def test_shutdown_stops_sessions(self):
        self.assertIn('self.dual_mqtt.stop()', inspect.getsource(SitdownApp.close))

    def test_broker_status_view_is_available(self):
        self.assertTrue(callable(getattr(SitdownApp, 'mqtt_network_status', None)))


if __name__ == '__main__':
    unittest.main()
