"""Gateway management and Broadlink discovery endpoints."""
import os
from typing import List, Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from ...devices import (
    DeviceStorage,
    GatewayCheckResult,
    GatewayInfo,
    GatewayStatus,
    ProviderCapability,
    ProviderCapabilityError,
    ProviderUnavailableError,
    UnsupportedProviderError,
    get_provider_for_gateway,
)
from ...devices.providers.broadlink_provider import BroadlinkProvider
from ...devices.providers.mock_provider import MockDeviceProvider
from ...locks import gateway_lock, ResourceBusyError

router = APIRouter(prefix="/api", tags=["Gateways"])


def get_provider():
    # Use mock provider only when explicitly enabled via environment
    if os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1":
        return MockDeviceProvider()
    provider = BroadlinkProvider()
    if not provider.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Broadlink SDK (python-broadlink) is not available. Please install python-broadlink or enable SMART_HUB_MOCK_HARDWARE=1 for simulation mode.",
        )
    return provider


class GatewaySaveRequest(BaseModel):
    id: str
    provider: str = "broadlink"
    model_name: str
    ip_address: str
    mac: str
    devtype: int
    name: str = Field(default="", description="Tên gateway, e.g. RM4 Phòng Khách")
    room: str = Field(default="", description="Phòng đặt thiết bị")
    is_locked: bool = False
    fwversion: Optional[int] = None


class DiscoverIpRequest(BaseModel):
    ip_address: str
    timeout: float = 4.0


@router.post("/gateway-discoveries")
def discover_gateways(timeout: float = 4.0):
    provider = get_provider()
    try:
        found = provider.discover(timeout=timeout)
        return {"gateways": [g.to_dict() for g in found]}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lỗi khi tìm kiếm gateway: {exc}",
        )


@router.post("/gateways/discover-ip")
def discover_by_ip(req: DiscoverIpRequest):
    provider = get_provider()
    try:
        gw = provider.discover_ip(req.ip_address, timeout=req.timeout)
        return gw.to_dict()
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"Thiết bị bị khóa: {exc}",
        )
    except TimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Quá thời gian kết nối tới {req.ip_address}: {exc}",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@router.get("/gateways")
def list_gateways():
    storage = DeviceStorage()
    return [g.to_dict() for g in storage.list_gateways()]


@router.post("/gateways")
def save_gateway(req: GatewaySaveRequest):
    storage = DeviceStorage()
    gw = GatewayInfo(
        id=req.id,
        provider=req.provider,
        model_name=req.model_name,
        ip_address=req.ip_address,
        mac=req.mac,
        devtype=req.devtype,
        name=req.name or f"Broadlink {req.model_name}",
        room=req.room or "Chưa phân phòng",
        is_locked=req.is_locked,
        fwversion=req.fwversion,
        status=GatewayStatus.ONLINE,
    )
    storage.save_gateway(gw)
    return gw.to_dict()


@router.get("/gateways/{gateway_id}")
def get_gateway(gateway_id: str):
    storage = DeviceStorage()
    gw = storage.get_gateway(gateway_id)
    if not gw:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy gateway '{gateway_id}'")
    return gw.to_dict()


@router.post("/gateways/{gateway_id}/checks")
def check_gateway(gateway_id: str):
    storage = DeviceStorage()
    gw = storage.get_gateway(gateway_id)
    if not gw:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy gateway '{gateway_id}'")

    try:
        provider = get_provider_for_gateway(gw)
    except UnsupportedProviderError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except ProviderUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    try:
        with gateway_lock(gw.id, timeout=0.0):
            res = provider.check_gateway(gw)
            storage.update_gateway_status(
                gw.id,
                status=res.status,
                is_locked=res.is_locked,
                fwversion=res.fwversion,
            )
            return {
                "gateway_id": gw.id,
                "is_online": res.is_online,
                "status": res.status.value,
                "message": res.message,
                "model_name": res.model_name,
                "is_locked": res.is_locked,
                "fwversion": res.fwversion,
                "checked_at": res.checked_at,
            }
    except ResourceBusyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Gateway đang bận xử lý một lệnh IR hoặc phiên học mã khác.",
        )
