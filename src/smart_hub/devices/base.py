"""Base definitions, dataclasses, and provider interfaces for devices and IR control."""
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any, Dict, List, Optional


class ApplianceCategory(str, Enum):
    TV = "tv"
    FAN = "fan"
    CLIMATE = "climate"
    CUSTOM = "custom"


class GatewayStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNREACHABLE = "unreachable"
    LOCKED = "locked"
    AUTH_FAILED = "auth_failed"
    UNKNOWN = "unknown"


class CommandState(str, Enum):
    PREPARED = "prepared"
    DISPATCHING = "dispatching"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ObservationOutcome(str, Enum):
    ACCURATE = "accurate"         # Đúng chức năng
    INACCURATE = "inaccurate"     # Không phản hồi / Sai
    UNKNOWN = "unknown"           # Chưa rõ kết quả


@dataclass
class GatewayInfo:
    id: str
    provider: str
    model_name: str
    ip_address: str
    mac: str
    devtype: int
    is_locked: bool = False
    name: str = ""
    room: str = ""
    status: GatewayStatus = GatewayStatus.ONLINE
    fwversion: Optional[int] = None
    last_checked_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GatewayCheckResult:
    is_online: bool
    status: GatewayStatus
    message: str
    devtype: Optional[int] = None
    model_name: Optional[str] = None
    is_locked: bool = False
    fwversion: Optional[int] = None
    checked_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())


@dataclass
class CodeRevision:
    id: str
    code_set_id: Optional[str]
    appliance_id: Optional[str]
    button_key: str
    button_name: str
    payload_base64: str
    payload_hash: str
    source_type: str            # 'catalog' or 'learned'
    revision_number: int = 1
    is_verified: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def compute_hash(cls, payload_bytes: bytes) -> str:
        return hashlib.sha256(payload_bytes).hexdigest()


@dataclass
class CodeSet:
    id: str
    category: ApplianceCategory
    brand: str
    models: List[str]
    source_name: str
    source_url: str
    source_revision: str
    license: str
    encoding: str               # 'broadlink_base64'
    hash: str
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    codes: Dict[str, str] = field(default_factory=dict)  # button_key -> base64 payload

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data


@dataclass
class Appliance:
    id: str
    name: str
    room: str
    category: ApplianceCategory
    brand: str
    model: str
    gateway_id: str
    code_set_id: Optional[str] = None
    mapping_revision: int = 1
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["category"] = self.category.value
        return data


@dataclass
class Observation:
    id: str
    appliance_id: str
    code_revision_id: str
    button_key: str
    outcome: ObservationOutcome
    user_notes: str = ""
    recorded_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["outcome"] = self.outcome.value
        return data


@dataclass
class CommandLedgerEntry:
    id: str
    request_id: str
    gateway_id: str
    appliance_id: str
    button_key: str
    code_revision_id: str
    state: CommandState
    sent_at: str
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    raw_ack: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data


class BaseDeviceProvider(ABC):
    """Abstract provider interface for IoT gateways (Broadlink, and Tuya in future)."""

    @abstractmethod
    def discover(self, timeout: float = 5.0) -> List[GatewayInfo]:
        """Scan LAN for available devices."""
        pass

    @abstractmethod
    def check_gateway(self, gateway: GatewayInfo) -> GatewayCheckResult:
        """Verify device reachability and auth without sending IR."""
        pass

    @abstractmethod
    def enter_learning(self, gateway: GatewayInfo, timeout: float = 30.0, cancel_token: Optional[Any] = None) -> bytes:
        """Put gateway into IR learning mode and poll until code received or timeout."""
        pass

    @abstractmethod
    def send_code(self, gateway: GatewayInfo, code_bytes: bytes) -> bool:
        """Send raw IR code bytes to appliance via gateway. Returns True on ACK."""
        pass

    @abstractmethod
    def close(self):
        """Clean up provider resources."""
        pass
