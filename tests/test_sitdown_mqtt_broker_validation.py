"""Offline broker input validation regression tests for dual MQTT."""
import configparser
import unittest
from sitdown_dual_mqtt import DualMQTTMonitors


class MQTTBrokerValidationTests(unittest.TestCase):
    def config(self, **settings):
        config = configparser.ConfigParser()
        config['mqtt_centralnj'] = {
            'enabled': 'true', 'topic': 'msh/US/2/e/test',
            **settings,
        }
        return config

    def test_blank_host_never_starts(self):
        manager = DualMQTTMonitors(self.config(host=' '))
        self.assertFalse(manager.connect('centralnj', factory=lambda: self.fail('unexpected connection')))
        self.assertIn('hostname', manager.snapshot()['centralnj']['status'])

    def test_whitespace_in_host_never_starts(self):
        manager = DualMQTTMonitors(self.config(host='bad host'))
        self.assertFalse(manager.connect('centralnj', factory=lambda: self.fail('unexpected connection')))

    def test_invalid_ports_never_start(self):
        for port in ('0', '65536', 'not-a-port'):
            with self.subTest(port=port):
                manager = DualMQTTMonitors(self.config(port=port))
                self.assertFalse(manager.connect('centralnj', factory=lambda: self.fail('unexpected connection')))
                self.assertIn('port', manager.snapshot()['centralnj']['status'])


if __name__ == '__main__':
    unittest.main()
