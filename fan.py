"""Support for KNX2/IP fans."""

from __future__ import annotations

import math
from typing import Any, Final

from xknx.devices import Fan as XknxFan

from homeassistant import config_entries
from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.const import CONF_ENTITY_CATEGORY, CONF_NAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import ConfigType
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from . import KNX2Module
from .const import KNX2_ADDRESS, KNX2_MODULE_KEY
from .entity import Knx2YamlEntity
from .schema import FanSchema

DEFAULT_PERCENTAGE: Final = 50


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: config_entries.ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up fan(s) for KNX2 platform."""
    knx2_module = hass.data[KNX2_MODULE_KEY]
    config: list[ConfigType] = knx2_module.config_yaml[Platform.FAN]

    async_add_entities(KNX2Fan(knx2_module, entity_config) for entity_config in config)


class KNX2Fan(Knx2YamlEntity, FanEntity):
    """Representation of a KNX2 fan."""

    _device: XknxFan
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, knx2_module: KNX2Module, config: ConfigType) -> None:
        """Initialize of KNX2 fan."""
        max_step = config.get(FanSchema.CONF_MAX_STEP)
        super().__init__(
            knx2_module=knx2_module,
            device=XknxFan(
                xknx=knx2_module.xknx,
                name=config[CONF_NAME],
                group_address_speed=config.get(KNX2_ADDRESS),
                group_address_speed_state=config.get(FanSchema.CONF_STATE_ADDRESS),
                group_address_oscillation=config.get(
                    FanSchema.CONF_OSCILLATION_ADDRESS
                ),
                group_address_oscillation_state=config.get(
                    FanSchema.CONF_OSCILLATION_STATE_ADDRESS
                ),
                max_step=max_step,
            ),
        )
        # FanSpeedMode.STEP if max_step is set
        self._step_range: tuple[int, int] | None = (1, max_step) if max_step else None
        self._attr_entity_category = config.get(CONF_ENTITY_CATEGORY)

        self._attr_unique_id = str(self._device.speed.group_address)

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan, as a percentage."""
        if self._step_range:
            step = math.ceil(percentage_to_ranged_value(self._step_range, percentage))
            await self._device.set_speed(step)
        else:
            await self._device.set_speed(percentage)

    @property
    def supported_features(self) -> FanEntityFeature:
        """Flag supported features."""
        flags = (
            FanEntityFeature.SET_SPEED
            | FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
        )

        if self._device.supports_oscillation:
            flags |= FanEntityFeature.OSCILLATE

        return flags

    @property
    def percentage(self) -> int | None:
        """Return the current speed as a percentage."""
        if self._device.current_speed is None:
            return None

        if self._step_range:
            return ranged_value_to_percentage(
                self._step_range, self._device.current_speed
            )
        return self._device.current_speed

    @property
    def speed_count(self) -> int:
        """Return the number of speeds the fan supports."""
        if self._step_range is None:
            return super().speed_count
        return int_states_in_range(self._step_range)

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        if percentage is None:
            await self.async_set_percentage(DEFAULT_PERCENTAGE)
        else:
            await self.async_set_percentage(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        await self.async_set_percentage(0)

    async def async_oscillate(self, oscillating: bool) -> None:
        """Oscillate the fan."""
        await self._device.set_oscillation(oscillating)

    @property
    def oscillating(self) -> bool | None:
        """Return whether or not the fan is currently oscillating."""
        return self._device.current_oscillation
