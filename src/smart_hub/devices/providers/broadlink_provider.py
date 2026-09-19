"""Broadlink RM4 provider implementing BaseDeviceProvider using python-broadlink."""
import logging
import socket
import threading
import time
from typing import Any, List, Optional

from ..base import (
    BaseDeviceProvider,
    GatewayCheckResult,
    GatewayInfo,
    GatewayStatus,
)

logger = logging.getLogger("smart_hub.devices.broadlink")


class ProviderUnavailableError(RuntimeError):
    """Raised when broadlink library is not installed or unavailable."""
    pass


class GatewayIdentityError(ValueError):
    """Raised when device at target IP does not match expected MAC or devtype (R03)."""
    pass


class GatewayTimeoutError(TimeoutError):
    """Raised when device communication times out."""
    pass


class GatewayLockedError(PermissionError):
    """Raised when device is locked in BroadLink mobile app."""
    pass


def send_packet_once(dev, packet_type: int, payload: bytes, timeout: float = 3.0) -> bytes:
    """Send exactly one UDP packet without retry loops on packet loss (R01 at-most-one attempt)."""
    dev.count = ((dev.count + 1) | 0x8000) & 0xFFFF
    packet = bytearray(0x38)
    packet[0x00:0x08] = bytes.fromhex("5aa5aa555aa5aa55")
    packet[0x24:0x26] = dev.devtype.to_bytes(2, "little")
    packet[0x26:0x28] = packet_type.to_bytes(2, "little")
    packet[0x28:0x2A] = dev.count.to_bytes(2, "little")
    packet[0x2A:0x30] = dev.mac[::-1]
    packet[0x30:0x34] = dev.id.to_bytes(4, "little")

    p_checksum = sum(payload, 0xBEAF) & 0xFFFF
    packet[0x34:0x36] = p_checksum.to_bytes(2, "little")

    padding = (16 - len(payload)) % 16
    encrypted_payload = dev.encrypt(payload + bytes(padding))
    packet.extend(encrypted_payload)

    checksum = sum(packet, 0xBEAF) & 0xFFFF
    packet[0x20:0x22] = checksum.to_bytes(2, "little")

    with dev.lock and socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as conn:
        conn.settimeout(timeout)
        conn.sendto(packet, dev.host)
        resp = conn.recvfrom(2048)[0]

    if len(resp) < 0x30:
        raise ValueError(f"Packet too short: {len(resp)} bytes")

    nom_checksum = int.from_bytes(resp[0x20:0x22], "little")
    real_checksum = sum(resp, 0xBEAF) - sum(resp[0x20:0x22]) & 0xFFFF

    if nom_checksum != real_checksum:
        raise ValueError(f"Checksum mismatch: expected {nom_checksum}, got {real_checksum}")

    return resp


class BroadlinkProvider(BaseDeviceProvider):
    """Provider for Broadlink RM devices (RM4 mini, RM4 pro, RM mini 3)."""

    def __init__(self):
        self._import_broadlink()

    def _import_broadlink(self):
        try:
            import broadlink
            self._broadlink = broadlink
        except ImportError:
            self._broadlink = None

    def is_available(self) -> bool:
        return self._broadlink is not None

    def discover(self, timeout: float = 5.0) -> List[GatewayInfo]:
        if not self._broadlink:
            raise RuntimeError("Thư viện broadlink chưa được cài đặt (.venv-dashboard).")
        try:
            raw_devices = self._broadlink.discover(timeout=int(timeout))
        except Exception as exc:
            logger.warning(f"Lỗi khi quét Broadlink trên LAN: {exc}")
            return []

        gateways = []
        for dev in raw_devices:
            mac_str = ":".join(f"{b:02x}" for b in dev.mac)
            ip_str = dev.host[0]
            devtype = getattr(dev, "devtype", 0)
            model_name = getattr(dev, "model", f"Broadlink RM ({hex(devtype)})")
            gw_id = f"gw_{mac_str.replace(':', '')[-8:]}"
            gateways.append(
                GatewayInfo(
                    id=gw_id,
                    provider="broadlink",
                    model_name=model_name,
                    ip_address=ip_str,
                    mac=mac_str,
                    devtype=devtype,
                    is_locked=getattr(dev, "is_locked", False),
                    status=GatewayStatus.ONLINE,
                    last_checked_at=None,
                )
            )
        return gateways

    def discover_ip(self, ip_address: str, timeout: float = 4.0) -> GatewayInfo:
        """Connect directly to a specific IP without broadcast scan."""
        if not self._broadlink:
            raise RuntimeError("Thư viện broadlink chưa được cài đặt (.venv-dashboard).")
        try:
            dev = self._broadlink.hello(ip_address, timeout=int(timeout))
            dev.auth()
            mac_str = ":".join(f"{b:02x}" for b in dev.mac)
            devtype = getattr(dev, "devtype", 0)
            model_name = getattr(dev, "model", f"Broadlink RM ({hex(devtype)})")
            fwversion = None
            try:
                fwversion = dev.get_fwversion()
            except Exception:
                pass
            is_locked = getattr(dev, "is_locked", False)
            gw_id = f"gw_{mac_str.replace(':', '')[-8:]}"
            return GatewayInfo(
                id=gw_id,
                provider="broadlink",
                model_name=model_name,
                ip_address=ip_address,
                mac=mac_str,
                devtype=devtype,
                is_locked=is_locked,
                fwversion=fwversion,
                status=GatewayStatus.ONLINE,
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "authentication" in err_msg or "locked" in err_msg:
                raise PermissionError(f"Thiết bị tại {ip_address} bị khóa hoặc lỗi xác thực: {exc}")
            elif "timeout" in err_msg:
                raise TimeoutError(f"Không thể kết nối tới {ip_address} (quá thời gian): {exc}")
            raise RuntimeError(f"Không thể kết nối tới {ip_address}: {exc}")

    def check_gateway(self, gateway: GatewayInfo) -> GatewayCheckResult:
        if not self._broadlink:
            return GatewayCheckResult(
                is_online=False,
                status=GatewayStatus.OFFLINE,
                message="Thư viện broadlink chưa được cài đặt.",
            )
        try:
            dev = self._broadlink.hello(gateway.ip_address, timeout=4)
            dev_mac = ":".join(f"{b:02x}" for b in dev.mac)
            if dev_mac.lower() != gateway.mac.lower():
                return GatewayCheckResult(
                    is_online=False,
                    status=GatewayStatus.UNREACHABLE,
                    message=f"IP {gateway.ip_address} đang trỏ sang thiết bị có MAC {dev_mac} khác {gateway.mac}!",
                )
            dev.auth()
            fwversion = None
            try:
                fwversion = dev.get_fwversion()
            except Exception:
                pass
            is_locked = getattr(dev, "is_locked", False)
            return GatewayCheckResult(
                is_online=True,
                status=GatewayStatus.ONLINE,
                message="Kết nối và xác thực thành công.",
                devtype=getattr(dev, "devtype", gateway.devtype),
                model_name=getattr(dev, "model", gateway.model_name),
                is_locked=is_locked,
                fwversion=fwversion,
            )
        except Exception as exc:
            err_msg = str(exc).lower()
            if "authentication" in err_msg or "locked" in err_msg:
                return GatewayCheckResult(
                    is_online=False,
                    status=GatewayStatus.LOCKED,
                    message=f"Thiết bị đang bị khóa trong app BroadLink. Vui lòng tắt 'Lock device' trong app.",
                )
            return GatewayCheckResult(
                is_online=False,
                status=GatewayStatus.UNREACHABLE,
                message=f"Không thể liên lạc gateway ({exc}).",
            )

    def _revalidate_and_connect(self, gateway: GatewayInfo, timeout: float = 4.0):
        if not self._broadlink:
            raise ProviderUnavailableError("Thư viện broadlink chưa được cài đặt (.venv-dashboard).")
        try:
            dev = self._broadlink.hello(gateway.ip_address, timeout=int(timeout))
        except Exception as exc:
            raise GatewayTimeoutError(f"Không thể kết nối gateway tại {gateway.ip_address}: {exc}") from exc

        # R03: Revalidate MAC
        dev_mac = ":".join(f"{b:02x}" for b in dev.mac)
        if dev_mac.lower() != gateway.mac.lower():
            raise GatewayIdentityError(
                f"Lỗi danh tính Gateway: IP {gateway.ip_address} có MAC {dev_mac} không khớp MAC đã lưu {gateway.mac}!"
            )

        # R03: Revalidate devtype if present
        dev_type = getattr(dev, "devtype", 0)
        if gateway.devtype and dev_type and dev_type != gateway.devtype:
            raise GatewayIdentityError(
                f"Lỗi danh tính Gateway: Thiết bị tại {gateway.ip_address} có devtype 0x{dev_type:x} khác 0x{gateway.devtype:x}!"
            )

        try:
            dev.auth()
        except Exception as exc:
            err_msg = str(exc).lower()
            if "authentication" in err_msg or "locked" in err_msg:
                raise GatewayLockedError(f"Thiết bị tại {gateway.ip_address} bị khóa hoặc từ chối xác thực: {exc}") from exc
            raise

        return dev

    def enter_learning(self, gateway: GatewayInfo, timeout: float = 30.0, cancel_token: Optional[threading.Event] = None) -> bytes:
        dev = self._revalidate_and_connect(gateway, timeout=5.0)
        dev.enter_learning()

        start_time = time.monotonic()
        while time.monotonic() - start_time < timeout:
            if cancel_token and cancel_token.is_set():
                raise InterruptedError("Người dùng đã hủy học mã.")
            time.sleep(1.0)
            try:
                data = dev.check_data()
                if data:
                    return bytes(data)
            except self._broadlink.exceptions.StorageError:
                # StorageError means no data received yet
                continue
            except Exception as exc:
                if "storage" in str(exc).lower():
                    continue
                raise

        raise TimeoutError(f"Hết thời gian chờ {timeout:.0f}s: Không nhận được tín hiệu IR từ remote gốc.")

    def send_code(self, gateway: GatewayInfo, code_bytes: bytes) -> bool:
        dev = self._revalidate_and_connect(gateway, timeout=4.0)
        # R01: Enforce at-most-one UDP attempt by wrapping send_packet
        dev.send_packet = lambda pt, pl: send_packet_once(dev, pt, pl, timeout=dev.timeout)
        try:
            dev.send_data(code_bytes)
        except (socket.timeout, TimeoutError) as exc:
            raise GatewayTimeoutError(f"Gửi lệnh IR quá thời gian chờ ACK từ thiết bị: {exc}") from exc
        return True

    def close(self):
        pass
