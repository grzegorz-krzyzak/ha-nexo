"""The integration ships its own icon."""

from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration


async def test_has_branding(hass: HomeAssistant) -> None:
    integration = await async_get_integration(hass, "nexo")
    assert integration.has_branding
