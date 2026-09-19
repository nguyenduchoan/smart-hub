"""Mock provider for unit tests and local development without hardware."""
import base64
import threading
import time
from typing import Dict, List, Optional

from ..base import (
    BaseDeviceProvider,
    GatewayCheckResult,
    GatewayInfo,
    GatewayStatus,
)


class MockDeviceProvider(BaseDeviceProvider):
    """Simulated Broadlink gateway for tests and offline mode."""

    def __init__(self):
        self.mock_devices: Dict[str, GatewayInfo] = {
            "gw_mock_rm4": GatewayInfo(
                id="gw_mock_rm4",
                provider="mock",
                model_name="Mock RM4 mini",
                ip_address="192.168.1.199",
                mac="24:df:a7:89:ab:cd",
                devtype=0x6508,
                is_locked=False,
                name="RM4 Phòng Khách (Mock)",
                room="Phòng Khách",
                status=GatewayStatus.ONLINE,
                fwversion=52079,
            )
        }
        self.learned_codes_queue: List[bytes] = [
            b"\x26\x00\x12\x00\x1f\x1f\x1f\x1f\x00\x0d\x05\x00\x00\x00\x00\x00",  # Fake IR pulse packet
        ]
        self.sent_codes: List[bytes] = []

    def discover(self, timeout: float = 5.0) -> List[GatewayInfo]:
        return list(self.mock_devices.values())

    def discover_ip(self, ip_address: str, timeout: float = 4.0) -> GatewayInfo:
        for gw in self.mock_devices.values():
            if gw.ip_address == ip_address:
                return gw
        return GatewayInfo(
            id=f"gw_{ip_address.replace('.', '_')}",
            provider="mock",
            model_name="Mock RM4 mini",
            ip_address=ip_address,
            mac="aa:bb:cc:dd:ee:ff",
            devtype=0x6508,
            status=GatewayStatus.ONLINE,
        )

    def check_gateway(self, gateway: GatewayInfo) -> GatewayCheckResult:
        if gateway.id not in self.mock_devices and gateway.ip_address != "192.168.1.199":
            return GatewayCheckResult(
                is_online=False,
                status=GatewayStatus.UNREACHABLE,
                message=f"Mock device at {gateway.ip_address} not reachable",
            )
        return GatewayCheckResult(
            is_online=True,
            status=GatewayStatus.ONLINE,
            message="Mock connection successful",
            devtype=0x6508,
            model_name="Mock RM4 mini",
            is_locked=False,
            fwversion=52079,
        )

    def enter_learning(self, gateway: GatewayInfo, timeout: float = 30.0, cancel_token: Optional[threading.Event] = None) -> bytes:
        start_time = time.monotonic()
        while time.monotonic() - start_time < 0.5:
            if cancel_token and cancel_token.is_set():
                raise InterruptedError("Người dùng đã hủy học mã.")
            time.sleep(0.05)
        if self.learned_codes_queue:
            return self.learned_codes_queue.pop(0)
        return b"\x26\x00\x12\x00\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa\xbb\xcc"

    def send_code(self, gateway: GatewayInfo, code_bytes: bytes) -> bool:
        self.sent_codes.append(code_bytes)
        return True

    def close(self):
        pass
