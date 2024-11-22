"""Support for KNX/IP switches."""

from __future__ import annotations

from typing import Any

from xknx.devices import Switch as XknxSwitch

from homeassistant import config_entries
from homeassistant.components.switch import SwitchEntity
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ENTITY_CATEGORY,
    CONF_NAME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import (
    AddEntitiesCallback,
    async_get_current_platform,
)
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.typing import ConfigType

from . import KNXModule
from .const import (
    CONF_INVERT,
    CONF_RESPOND_TO_READ,
    CONF_SYNC_STATE,
    DOMAIN,
    KNX_ADDRESS,
    KNX_MODULE_KEY,
)
from .entity import Knx2UiEntity, Knx2UiEntityPlatformController, Knx2YamlEntity
from .schema import SwitchSchema
from .storage.const import (
    CONF_ENTITY,
    CONF_GA_PASSIVE,
    CONF_GA_STATE,
    CONF_GA_SWITCH,
    CONF_GA_WRITE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up switch(es) for KNX platform."""
    knx2_module = hass.data[KNX2_MODULE_KEY]
    platform = async_get_current_platform()
    knx2_module.config_store.add_platform(
        platform=Platform.SWITCH,
        controller=Knx2UiEntityPlatformController(
            knx2_module=knx2_module,
            entity_platform=platform,
            entity_class=Knx2UiSwitch,
        ),
    )

    entities: list[Knx2YamlEntity | Knx2UiEntity] = []
    if yaml_platform_config := knx2_module.config_yaml.get(Platform.SWITCH):
        entities.extend(
            Knx2YamlSwitch(knx2_module, entity_config)
            for entity_config in yaml_platform_config
        )
    if ui_config := knx2_module.config_store.data["entities"].get(Platform.SWITCH):
        entities.extend(
            Knx2UiSwitch(knx2_module, unique_id, config)
            for unique_id, config in ui_config.items()
        )
    if entities:
        async_add_entities(entities)


class _Knx2Switch(SwitchEntity, RestoreEntity):
    """Base class for a KNX switch."""

    _device: XknxSwitch

    async def async_added_to_hass(self) -> None:
        """Restore last state."""
        await super().async_added_to_hass()
        if not self._device.switch.readable and (
            last_state := await self.async_get_last_state()
        ):
            if last_state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._device.switch.value = last_state.state == STATE_ON

    @property
    def is_on(self) -> bool:
        """Return true if device is on."""
        return bool(self._device.state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the device on."""
        await self._device.set_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the device off."""
        await self._device.set_off()


class Knx2YamlSwitch(_Knx2Switch, Knx2YamlEntity):
    """Representation of a KNX switch configured from YAML."""

    _device: XknxSwitch

    def __init__(self, knx2_module: KNXModule, config: ConfigType) -> None:
        """Initialize of KNX switch."""
        super().__init__(
            knx2_module=knx2_module,
            device=XknxSwitch(
                xknx=knx2_module.xknx,
                name=config[CONF_NAME],
                group_address=config[KNX_ADDRESS],
                group_address_state=config.get(SwitchSchema.CONF_STATE_ADDRESS),
                respond_to_read=config[CONF_RESPOND_TO_READ],
                invert=config[SwitchSchema.CONF_INVERT],
            ),
        )
        self._attr_entity_category = config.get(CONF_ENTITY_CATEGORY)
        self._attr_device_class = config.get(CONF_DEVICE_CLASS)
        self._attr_unique_id = str(self._device.switch.group_address)


class Knx2UiSwitch(_Knx2Switch, Knx2UiEntity):
    """Representation of a KNX switch configured from UI."""

    _device: XknxSwitch

    def __init__(
        self, knx2_module: KNXModule, unique_id: str, config: dict[str, Any]
    ) -> None:
        """Initialize KNX switch."""
        super().__init__(
            knx2_module=knx2_module,
            unique_id=unique_id,
            entity_config=config[CONF_ENTITY],
        )
        self._device = XknxSwitch(
            knx2_module.xknx,
            name=config[CONF_ENTITY][CONF_NAME],
            group_address=config[DOMAIN][CONF_GA_SWITCH][CONF_GA_WRITE],
            group_address_state=[
                config[DOMAIN][CONF_GA_SWITCH][CONF_GA_STATE],
                *config[DOMAIN][CONF_GA_SWITCH][CONF_GA_PASSIVE],
            ],
            respond_to_read=config[DOMAIN][CONF_RESPOND_TO_READ],
            sync_state=config[DOMAIN][CONF_SYNC_STATE],
            invert=config[DOMAIN][CONF_INVERT],
        )
