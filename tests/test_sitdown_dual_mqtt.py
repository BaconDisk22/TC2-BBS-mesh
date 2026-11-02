"""No-broker tests for independent, default-off MQTT profiles."""
import configparser
import unittest
from sitdown_dual_mqtt import DualMQTTMonitors, network_config


class FakeClient:
    def __init__(self):
        self.connected = []
        self.subscriptions = []
        self.stopped = False

    def username_pw_set(self, *args): pass
    def tls_set(self, **kwargs): pass
    def reconnect_delay_set(self, **kwargs): pass
    def connect_async(self, host, port, keepalive):
        self.connected.append((host, port))
    def loop_start(self): pass
    def disconnect(self): pass
    def loop_stop(self): self.stopped = True
    def subscribe(self, topic):
        self.subscriptions.append(topic)
        return (0, 1)


class DualMQTTTests(unittest.TestCase):
    def test_defaults_never_connect(self):
        manager = DualMQTTMonitors(configparser.ConfigParser())
        manager.start(factories={'centralnj': lambda: self.fail('connected'),
                                 'sjmesh': lambda: self.fail('connected')})
        self.assertEqual(manager.snapshot()['centralnj']['total'], 0)

    def test_both_clients_are_independent(self):
        config = configparser.ConfigParser()
        for section in ('mqtt_centralnj', 'mqtt_sjmesh'):
            config[section] = {'enabled': 'true', 'topic': 'msh/US/2/e/test'}
        clients = {name: FakeClient() for name in ('centralnj', 'sjmesh')}
        manager = DualMQTTMonitors(config)
        manager.start(factories={name: (lambda n=name: clients[n]) for name in clients})
        self.assertEqual(clients['centralnj'].connected[0][0], 'mqtt.meshtastic.org')
        self.assertEqual(clients['sjmesh'].connected[0][0], 'mqtt.sjmesh.net')
        manager.stop('centralnj')
        self.assertTrue(clients['centralnj'].stopped)
        self.assertFalse(clients['sjmesh'].stopped)
        manager.stop()

    def test_manual_connect_does_not_start_other_network(self):
        config = configparser.ConfigParser()
        for section in ('mqtt_centralnj', 'mqtt_sjmesh'):
            config[section] = {'enabled': 'true', 'topic': 'msh/US/2/e/test'}
        manager = DualMQTTMonitors(config)
        client = FakeClient()
        self.assertTrue(manager.connect('centralnj', factory=lambda: client))
        self.assertEqual(len(client.connected), 1)
        self.assertEqual(manager.snapshot()['sjmesh']['status'], 'Disabled')
        manager.stop()
        self.assertTrue(client.stopped)

    def test_disabled_profile_refuses_manual_connection(self):
        config = configparser.ConfigParser()
        config['mqtt_centralnj'] = {'enabled': 'false', 'topic': 'msh/US/2/e/test'}
        manager = DualMQTTMonitors(config)
        self.assertFalse(manager.connect('centralnj', factory=lambda: self.fail('unexpected connect')))

    def test_wildcard_topic_refuses_manual_connection(self):
        config = configparser.ConfigParser()
        config['mqtt_sjmesh'] = {'enabled': 'true', 'topic': 'msh/US/2/e/#'}
        manager = DualMQTTMonitors(config)
        self.assertFalse(manager.connect('sjmesh', factory=lambda: self.fail('unexpected connect')))

    def test_broad_topic_never_connects(self):
        config = configparser.ConfigParser()
        config['mqtt_sjmesh'] = {'enabled': 'true', 'topic': 'msh/US/#'}
        manager = DualMQTTMonitors(config)
        manager.start(factories={'sjmesh': lambda: self.fail('unsafe connection')})
        self.assertIn('narrow topic', manager.snapshot()['sjmesh']['status'])

    def test_no_credentials_in_diagnostics(self):
        config = configparser.ConfigParser()
        config['mqtt_centralnj'] = {'enabled': 'true', 'topic': 'msh/US/2/e/test',
                                    'username': 'private-user', 'password': 'private-pass'}
        output = str(DualMQTTMonitors(config).diagnostics())
        self.assertNotIn('private-user', output)
        self.assertNotIn('private-pass', output)

    def test_explicit_disabled_profile(self):
        config = configparser.ConfigParser()
        config['mqtt_centralnj'] = {'enabled': 'false', 'topic': 'msh/US/2/e/test'}
        self.assertFalse(network_config(config, 'centralnj').getboolean('mqtt_monitor', 'enabled'))


if __name__ == '__main__':
    unittest.main()
