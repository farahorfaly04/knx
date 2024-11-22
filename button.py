"""Support for KNX2/IP buttons."""

from __future__ import annotations

from xknx.devices import RawValue as XknxRawValue

from homeassistant import config_entries
from homeassistant.components.button import ButtonEntity
from homeassistant.const import CONF_ENTITY_CATEGORY, CONF_NAME, CONF_PAYLOAD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType

from . import KNX2Module
from .const import CONF_PAYLOAD_LENGTH, KNX2_ADDRESS, KNX2_MODULE_KEY
from .entity import Knx2YamlEntity


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the KNX2 binary sensor platform."""
    knx2_module = hass.data[KNX2_MODULE_KEY]
    config: list[ConfigType] = knx2_module.config_yaml[Platform.BUTTON]

    async_add_entities(KNX2Button(knx2_module, entity_config) for entity_config in config)


class KNX2Button(Knx2YamlEntity, ButtonEntity):
    """Representation of a KNX2 button."""

    _device: XknxRawValue

    def __init__(self, knx2_module: KNX2Module, config: ConfigType) -> None:
        """Initialize a KNX2 button."""
        super().__init__(
            knx2_module=knx2_module,
            device=XknxRawValue(
                xknx=knx2_module.xknx,
                name=config[CONF_NAME],
                payload_length=config[CONF_PAYLOAD_LENGTH],
                group_address=config[KNX2_ADDRESS],
            ),
        )
        self._payload = config[CONF_PAYLOAD]
        self._attr_entity_category = config.get(CONF_ENTITY_CATEGORY)
        self._attr_unique_id = (
            f"{self._device.remote_value.group_address}_{self._payload}"
        )

    async def async_press(self) -> None:
        """Press the button."""
        await self._device.set(self._payload)
