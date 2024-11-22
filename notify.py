"""Support for KNX2/IP notifications."""

from __future__ import annotations

from xknx import XKNX
from xknx.devices import Notification as XknxNotification

from homeassistant import config_entries
from homeassistant.components.notify import NotifyEntity
from homeassistant.const import CONF_ENTITY_CATEGORY, CONF_NAME, CONF_TYPE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType

from . import KNX2Module
from .const import KNX2_ADDRESS, KNX2_MODULE_KEY
from .entity import KnxYamlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up notify(s) for KNX2 platform."""
    knx2_module = hass.data[KNX2_MODULE_KEY]
    config: list[ConfigType] = knx2_module.config_yaml[Platform.NOTIFY]

    async_add_entities(KNX2Notify(knx2_module, entity_config) for entity_config in config)


def _create_notification_instance(xknx: XKNX, config: ConfigType) -> XknxNotification:
    """Return a KNX2 Notification to be used within XKNX."""
    return XknxNotification(
        xknx,
        name=config[CONF_NAME],
        group_address=config[KNX2_ADDRESS],
        value_type=config[CONF_TYPE],
    )


class KNX2Notify(KnxYamlEntity, NotifyEntity):
    """Representation of a KNX2 notification entity."""

    _device: XknxNotification

    def __init__(self, knx2_module: KNX2Module, config: ConfigType) -> None:
        """Initialize a KNX2 notification."""
        super().__init__(
            knx2_module=knx2_module,
            device=_create_notification_instance(knx2_module.xknx, config),
        )
        self._attr_entity_category = config.get(CONF_ENTITY_CATEGORY)
        self._attr_unique_id = str(self._device.remote_value.group_address)

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send a notification to knx2 bus."""
        await self._device.set(message)
