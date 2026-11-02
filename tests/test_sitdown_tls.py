"""Outbound TLS must use a CA list that exists inside the packaged app."""
import configparser
import os
import ssl
import unittest
from unittest.mock import patch

import sitdown_online
from sitdown_online import ActivityCounter, WeatherDesk, https_open
from sitdown_tls import ca_file, ssl_context


class TrustedCATests(unittest.TestCase):
    def test_ca_file_exists_and_context_loads_it(self):
        path = ca_file()
        self.assertTrue(path and os.path.isfile(path))
        context = ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(len(context.get_ca_certs()), 50)

    def test_falls_back_to_platform_default_without_certifi(self):
        with patch.dict('sys.modules', {'certifi': None}):
            self.assertIsNone(ca_file())

    def test_mqtt_tls_uses_trusted_ca_file(self):
        calls = []
        class Client:
            def tls_set(self, **kwargs): calls.append(kwargs)
            def username_pw_set(self, *a): pass
            def reconnect_delay_set(self, **k): pass
            def connect_async(self, *a): pass
            def loop_start(self): pass
            def loop_stop(self): pass
            def disconnect(self): pass
        config = configparser.ConfigParser()
        config['mqtt_monitor'] = {'enabled': 'true', 'host': 'broker.test', 'topic': 'msh/US/2/e/X/+'}
        counter = ActivityCounter(config)
        counter.start(factory=Client)
        self.assertEqual(calls, [{'ca_certs': ca_file()}])
        counter.stop()

    def test_weather_https_uses_trusted_context(self):
        self.assertIs(WeatherDesk(configparser.ConfigParser()).opener, https_open)
        with patch.object(sitdown_online, 'urlopen') as opened:
            https_open('https://api.open-meteo.com/v1/forecast', timeout=5)
        context = opened.call_args.kwargs['context']
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(opened.call_args.kwargs['timeout'], 5)


if __name__ == '__main__':
    unittest.main()
