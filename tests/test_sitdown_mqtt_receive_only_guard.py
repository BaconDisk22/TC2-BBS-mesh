"""Guard the current receive-only MQTT implementation from accidental publishing."""
import inspect
import unittest
from sitdown_online import ActivityCounter
from sitdown_dual_mqtt import DualMQTTMonitors


class ReceiveOnlySafetyTests(unittest.TestCase):
    def test_activity_counter_has_no_publish_method(self):
        self.assertFalse(hasattr(ActivityCounter, 'publish'))
        source = inspect.getsource(ActivityCounter)
        self.assertNotIn('.publish(', source)

    def test_dual_monitor_has_no_publish_method(self):
        self.assertFalse(hasattr(DualMQTTMonitors, 'publish'))
        source = inspect.getsource(DualMQTTMonitors)
        self.assertNotIn('.publish(', source)

    def test_activity_counter_does_not_forward_messages(self):
        source = inspect.getsource(ActivityCounter._message)
        self.assertNotIn('.sendText(', source)
        self.assertNotIn('.publish(', source)


if __name__ == '__main__':
    unittest.main()
