"""Pure permission checks for the operator-controlled MQTT sender (sitdown_mqtt_sender).

This module does not publish anything itself.
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
    return SendDecision(True, 'Allowed')
