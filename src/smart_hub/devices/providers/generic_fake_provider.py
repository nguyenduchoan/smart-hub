"""Generic fake provider demonstrating provider-neutral contract (T35 / AC-28).
Does not require MAC address or raw IR, performs non-IR actions, and returns observed state.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import (
    BaseDeviceProvider,
    GatewayCheckResult,
    GatewayInfo,
    GatewayStatus,
    ProviderCapability,
)


class GenericFakeProvider(BaseDeviceProvider):
    """Generic IoT device provider (e.g., smart plug, relay, HTTP/MQTT endpoint)."""

    def __init__(self):
        self.observed_states: Dict[str, Dict[str, Any]] = {}
        self.action_history: List[Dict[str, Any]] = []

    @property
    def capabilities(self) -> List[ProviderCapability]:
        return [
            ProviderCapability.DIRECT_COMMAND,
            ProviderCapability.STATE_QUERY,
        ]

    def discover(self, timeout: float = 5.0) -> List[GatewayInfo]:
        return [
            GatewayInfo(
                id="gw_generic_fake",
                provider="generic_fake",
                model_name="Generic Smart Switch",
                ip_address="192.168.1.200",
                mac="",
                devtype=0,
                name="Generic Switch",
                status=GatewayStatus.ONLINE,
            )
        ]

    def check_gateway(self, gateway: GatewayInfo) -> GatewayCheckResult:
        return GatewayCheckResult(
            is_online=True,
            status=GatewayStatus.ONLINE,
            message="Generic device reachable and authenticated",
            model_name="Generic Smart Switch",
        )

    def execute_action(self, gateway: GatewayInfo, action_type: str, params: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        current = self.observed_states.setdefault(gateway.id, {"power": "off", "mode": "idle"})
        if action_type in ("set_power", "power"):
            val = params.get("power", "on")
            current["power"] = val
        elif action_type == "toggle":
            current["power"] = "off" if current.get("power") == "on" else "on"
        elif action_type == "query_state":
            pass
        else:
            current["last_action"] = action_type
            current.update(params)

        current["updated_at"] = now
        entry = {
            "gateway_id": gateway.id,
            "action_type": action_type,
            "params": params,
            "timestamp": now,
            "state": dict(current),
        }
        self.action_history.append(entry)
        return {
            "delivered": True,
            "action_type": action_type,
            "observed_state": dict(current),
        }

    def send_direct_command(self, gateway: GatewayInfo, command: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        res = self.execute_action(gateway, command, params or {})
        return {
            "success": res.get("delivered", True),
            "command": command,
            "observed_state": res.get("observed_state", {}),
        }

    def query_state(self, gateway: GatewayInfo) -> Dict[str, Any]:
        res = self.execute_action(gateway, "query_state", {})
        return res.get("observed_state", {})

    def close(self):
        pass
