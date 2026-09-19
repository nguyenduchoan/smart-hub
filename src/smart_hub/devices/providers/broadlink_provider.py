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

    def enter_learning(self, gateway: GatewayInfo, timeout: float = 30.0, cancel_token: Optional[threading.Event] = None) -> bytes:
        if not self._broadlink:
            raise RuntimeError("Thư viện broadlink chưa được cài đặt.")
        dev = self._broadlink.hello(gateway.ip_address, timeout=5)
        dev.auth()
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
        if not self._broadlink:
            raise RuntimeError("Thư viện broadlink chưa được cài đặt.")
        dev = self._broadlink.hello(gateway.ip_address, timeout=4)
        dev.auth()
        dev.send_data(code_bytes)
        return True

    def close(self):
        pass
