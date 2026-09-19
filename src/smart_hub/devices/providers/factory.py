"""Provider factory and capability resolution for IoT gateways (AC-28)."""
import os
from typing import Optional

from ..base import (
    BaseDeviceProvider,
    GatewayInfo,
    ProviderCapability,
    ProviderUnavailableError,
    UnsupportedProviderError,
)
from .broadlink_provider import BroadlinkProvider
from .generic_fake_provider import GenericFakeProvider
from .mock_provider import MockDeviceProvider


def get_provider_for_gateway(gateway: GatewayInfo) -> BaseDeviceProvider:
    """Resolve and instantiate device provider for a gateway, validating provider support and mode."""
    provider_name = (gateway.provider or "").strip().lower()

    if provider_name == "broadlink":
        if os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1":
            return MockDeviceProvider()
        provider = BroadlinkProvider()
        if not provider.is_available():
            raise ProviderUnavailableError(
                "Broadlink SDK (python-broadlink) is not available. "
                "Vui lòng cài đặt python-broadlink hoặc bật SMART_HUB_MOCK_HARDWARE=1 cho chế độ giả lập."
            )
        return provider

    if provider_name == "mock":
        if os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1":
            return MockDeviceProvider()
        raise UnsupportedProviderError(
            "Provider 'mock' chỉ được phép sử dụng trong môi trường giả lập (SMART_HUB_MOCK_HARDWARE=1)."
        )

    if provider_name == "generic_fake":
        if os.environ.get("SMART_HUB_MOCK_HARDWARE") == "1":
            return GenericFakeProvider()
        raise UnsupportedProviderError(
            "Provider 'generic_fake' chỉ được phép sử dụng trong môi trường giả lập (SMART_HUB_MOCK_HARDWARE=1)."
        )

    raise UnsupportedProviderError(
        f"Provider '{gateway.provider}' không được hỗ trợ hoặc không xác định. "
        f"Chỉ hỗ trợ 'broadlink' (hoặc 'mock' / 'generic_fake' khi bật SMART_HUB_MOCK_HARDWARE=1)."
    )
