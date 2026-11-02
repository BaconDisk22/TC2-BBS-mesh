"""Up to four comma-separated topics per network, each held to the single-topic rule."""
import configparser
import unittest
from sitdown_dual_mqtt import MAX_TOPICS, DualMQTTMonitors, topics_allowed
from sitdown_online import topic_list


class Client:
    def __init__(self, codes=None):
        self.subscribed, self.codes = [], list(codes or [])
    def username_pw_set(self, *a): pass
    def tls_set(self, **k): pass
    def reconnect_delay_set(self, **k): pass
    def connect_async(self, *a): pass
    def loop_start(self): pass
    def loop_stop(self): pass
    def disconnect(self): pass
    def subscribe(self, topic):
        self.subscribed.append(topic)
        return (self.codes.pop(0) if self.codes else 0, len(self.subscribed))


def manager(topic):
    config = configparser.ConfigParser()
    config['mqtt_sjmesh'] = {'enabled': 'true', 'topic': topic}
    return DualMQTTMonitors(config)


class TopicListTests(unittest.TestCase):
    def test_split_trims_and_skips_blanks(self):
        self.assertEqual(topic_list(' a/b , ,c/d/+ ,'), ['a/b', 'c/d/+'])
        self.assertEqual(topic_list(''), [])

    def test_allowed_lists(self):
        for value in ('msh/US/2/e/SJMesh/+', 'msh/US/2/e/SJMesh/+, msh/US/2/e/LongFast/+',
                      ', '.join(f'msh/US/2/e/Ch{i}/+' for i in range(MAX_TOPICS))):
            with self.subTest(value=value):
                self.assertTrue(topics_allowed(value))

    def test_refused_lists(self):
        for value in ('', ' , ', 'msh/US/2/e/SJMesh/+, msh/US/#', 'msh/US/2/e/SJMesh/+, msh/US/2/e/+',
                      'msh/US/2/e/A/+, msh/US/2/e/A/+',
                      ', '.join(f'msh/US/2/e/Ch{i}/+' for i in range(MAX_TOPICS + 1))):
            with self.subTest(value=value):
                self.assertFalse(topics_allowed(value))


class SubscribeTests(unittest.TestCase):
    def test_each_topic_is_subscribed_once(self):
        m = manager('msh/US/2/e/SJMesh/+, msh/US/2/e/LongFast/+')
        client = Client()
        self.assertTrue(m.connect('sjmesh', factory=lambda: client))
        m.monitors['sjmesh']._connect(client, None, None, 0, None)
        self.assertEqual(client.subscribed, ['msh/US/2/e/SJMesh/+', 'msh/US/2/e/LongFast/+'])
        self.assertEqual(m.snapshot()['sjmesh']['status'], 'Subscription requested (not yet confirmed)')
        m.stop()

    def test_any_failed_subscription_is_reported(self):
        m = manager('msh/US/2/e/SJMesh/+, msh/US/2/e/LongFast/+')
        client = Client(codes=[0, 4])
        m.connect('sjmesh', factory=lambda: client)
        m.monitors['sjmesh']._connect(client, None, None, 0, None)
        self.assertEqual(m.snapshot()['sjmesh']['status'], 'Subscription failed')
        m.stop()

    def test_one_bad_entry_blocks_the_whole_profile(self):
        m = manager('msh/US/2/e/SJMesh/+, msh/US/#')
        self.assertFalse(m.connect('sjmesh', factory=lambda: self.fail('unsafe connection')))
        self.assertIn('narrow topic', m.snapshot()['sjmesh']['status'])


if __name__ == '__main__':
    unittest.main()
