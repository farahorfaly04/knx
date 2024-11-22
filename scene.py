"""Support for KNX2 scenes."""

from __future__ import annotations

from typing import Any

from xknx.devices import Scene as XknxScene

from homeassistant import config_entries
from homeassistant.components.scene import Scene
from homeassistant.const import CONF_ENTITY_CATEGORY, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType

from . import KNX2Module
from .const import KNX2_ADDRESS, KNX2_MODULE_KEY
from .entity import KnxYamlEntity
from .schema import SceneSchema


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up scene(s) for KNX2 platform."""
    knx2_module = hass.data[KNX2_MODULE_KEY]
    config: list[ConfigType] = knx2_module.config_yaml[Platform.SCENE]

    async_add_entities(KNX2Scene(knx2_module, entity_config) for entity_config in config)


class KNX2Scene(KnxYamlEntity, Scene):
    """Representation of a KNX2 scene."""

    _device: XknxScene

    def __init__(self, knx2_module: KNX2Module, config: ConfigType) -> None:
        """Init KNX2 scene."""
        super().__init__(
            knx2_module=knx2_module,
            device=XknxScene(
                xknx=knx2_module.xknx,
                name=config[CONF_NAME],
                group_address=config[KNX2_ADDRESS],
                scene_number=config[SceneSchema.CONF_SCENE_NUMBER],
            ),
        )
        self._attr_entity_category = config.get(CONF_ENTITY_CATEGORY)
        self._attr_unique_id = (
            f"{self._device.scene_value.group_address}_{self._device.scene_number}"
        )

    async def async_activate(self, **kwargs: Any) -> None:
        """Activate the scene."""
        await self._device.run()
