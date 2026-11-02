"""Offline topic policy: exact topics, or one channel from every gateway only."""
import configparser
import unittest
from sitdown_dual_mqtt import DualMQTTMonitors, topic_allowed


class TopicPolicyTests(unittest.TestCase):
    def test_exact_topics_are_allowed(self):
        for topic in ('msh/US/2/e/CentralNJ/!abcd1234', 'msh/US/2/e/LongFast', 'custom/feed'):
            with self.subTest(topic=topic):
                self.assertTrue(topic_allowed(topic))

    def test_channel_scoped_gateway_wildcard_is_allowed(self):
        for topic in ('msh/US/2/e/CentralNJ/+', 'msh/US/NJ/2/e/CentralNJ/+'):
            with self.subTest(topic=topic):
                self.assertTrue(topic_allowed(topic))

    def test_broad_or_misplaced_wildcards_are_refused(self):
        for topic in ('', ' ', '#', 'msh/#', 'msh/US/#', 'msh/US/2/e/#', 'msh/US/2/e/CentralNJ/#',
                      '+', 'msh/+', 'msh/US/2/e/+', 'msh/US/2/e/+/+', 'msh/+/2/e/CentralNJ/+',
                      'msh/US/2/json/CentralNJ/+', 'msh/US/2/c/CentralNJ/+', 'other/US/2/e/CentralNJ/+',
                      'msh/US/2/e/CentralNJ/!ab+', 'msh/US/2/e/Central+NJ/+', 'msh/US//2/e/CentralNJ/+',
                      'msh/US/2/e/Central NJ/+', 'msh/US/2/e/CentralNJ/+/extra'):
            with self.subTest(topic=topic):
                self.assertFalse(topic_allowed(topic))

    def test_manager_uses_the_same_rule(self):
        config = configparser.ConfigParser()
        config['mqtt_centralnj'] = {'enabled': 'true', 'topic': 'msh/US/2/e/CentralNJ/+'}
        config['mqtt_sjmesh'] = {'enabled': 'true', 'topic': 'msh/US/2/e/+'}
        subscribed = []
        class Client:
            def username_pw_set(self, *a): pass
            def tls_set(self, **kwargs): pass
            def reconnect_delay_set(self, **k): pass
            def connect_async(self, *a): pass
            def loop_start(self): pass
            def loop_stop(self): pass
            def disconnect(self): pass
            def subscribe(self, topic):
                subscribed.append(topic)
                return (0, 1)
        manager = DualMQTTMonitors(config)
        client = Client()
        self.assertTrue(manager.connect('centralnj', factory=lambda: client))
        manager.monitors['centralnj']._connect(client, None, None, 0, None)
        self.assertEqual(subscribed, ['msh/US/2/e/CentralNJ/+'])
        self.assertFalse(manager.connect('sjmesh', factory=lambda: self.fail('unsafe connection')))
        self.assertIn('narrow topic', manager.snapshot()['sjmesh']['status'])
        manager.stop()


if __name__ == '__main__':
    unittest.main()
