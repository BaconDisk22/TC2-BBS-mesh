"""Trusted CA certificates for outbound TLS (MQTT brokers, internet weather).

The packaged Mac app's OpenSSL looks for CA files inside a Python.framework
folder that does not exist on most Macs, so every certificate check fails.
Use certifi's bundled list when it is available; otherwise fall back to the
platform default.
"""
import ssl


def ca_file():
    try:
        import certifi
    except ImportError:
        return None
    return certifi.where()


def ssl_context():
    return ssl.create_default_context(cafile=ca_file())
