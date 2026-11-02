"""Pure permission checks for a future operator-controlled MQTT sender.

This module does not implement MQTT publishing or radio transmission.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SendDecision:
    allowed: bool
    reason: str


def check_send_permission(config, network, *, demo=False, session_confirmed=False):
    """Deny by default; require explicit network and session permission."""
    if network not in ('centralnj', 'sjmesh'):
        return SendDecision(False, 'Unknown network')
    if demo:
        return SendDecision(False, 'Demo mode cannot transmit')
    section = 'mqtt_' + network
    if not config.getboolean(section, 'enabled', fallback=False):
        return SendDecision(False, 'Network is disabled')
    if not config.getboolean(section, 'allow_publish', fallback=False):
        return SendDecision(False, 'Outbound MQTT publishing is disabled')
    if not session_confirmed:
        return SendDecision(False, 'Operator confirmation required this session')
    return SendDecision(True, 'Policy permits a future sender; no publisher is implemented')
