"""Support for KNX2/IP text."""

from __future__ import annotations

from xknx import XKNX
from xknx.devices import Notification as XknxNotification
from xknx.dpt import DPTLatin1

from homeassistant import config_entries
from homeassistant.components.text import TextEntity
from homeassistant.const import (
    CONF_ENTITY_CATEGORY,
    CONF_MODE,
    CONF_NAME,
    CONF_TYPE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType

from . import KNX2Module
from .const import CONF_RESPOND_TO_READ, CONF_STATE_ADDRESS, KNX2_ADDRESS, KNX2_MODULE_KEY
from .entity import Knx2YamlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor(s) for KNX2 platform."""
    knx2_module = hass.data[KNX2_MODULE_KEY]
    config: list[ConfigType] = knx2_module.config_yaml[Platform.TEXT]

    async_add_entities(KNX2Text(knx2_module, entity_config) for entity_config in config)


def _create_notification(xknx: XKNX, config: ConfigType) -> XknxNotification:
    """Return a KNX2 Notification to be used within XKNX."""
    return XknxNotification(
        xknx,
        name=config[CONF_NAME],
        group_address=config[KNX2_ADDRESS],
        group_address_state=config.get(CONF_STATE_ADDRESS),
        respond_to_read=config[CONF_RESPOND_TO_READ],
        value_type=config[CONF_TYPE],
    )


class KNX2Text(Knx2YamlEntity, TextEntity, RestoreEntity):
    """Representation of a KNX2 text."""

    _device: XknxNotification
    _attr_native_max = 14

    def __init__(self, knx2_module: KNX2Module, config: ConfigType) -> None:
        """Initialize a KNX2 text."""
        super().__init__(
            knx2_module=knx2_module,
            device=_create_notification(knx2_module.xknx, config),
        )
        self._attr_mode = config[CONF_MODE]
        self._attr_pattern = (
            r"[\u0000-\u00ff]*"  # Latin-1
            if issubclass(self._device.remote_value.dpt_class, DPTLatin1)
            else r"[\u0000-\u007f]*"  # ASCII
        )
        self._attr_entity_category = config.get(CONF_ENTITY_CATEGORY)
        self._attr_unique_id = str(self._device.remote_value.group_address)

    async def async_added_to_hass(self) -> None:
        """Restore last state."""
        await super().async_added_to_hass()
        if not self._device.remote_value.readable and (
            last_state := await self.async_get_last_state()
        ):
            if last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._device.remote_value.value = last_state.state

    @property
    def native_value(self) -> str | None:
        """Return the value reported by the text."""
        return self._device.message

    async def async_set_value(self, value: str) -> None:
        """Change the value."""
        await self._device.set(value)
