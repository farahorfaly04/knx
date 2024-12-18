"""Base class for KNX2 devices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xknx.devices import Device as XknxDevice

from homeassistant.const import CONF_ENTITY_CATEGORY, EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import EntityPlatform
from homeassistant.helpers.entity_registry import RegistryEntry

from .const import DOMAIN
from .storage.config_store import PlatformControllerBase
from .storage.const import CONF_DEVICE_INFO

if TYPE_CHECKING:
    from . import KNX2Module


class Knx2UiEntityPlatformController(PlatformControllerBase):
    """Class to manage dynamic adding and reloading of UI entities."""

    def __init__(
        self,
        knx2_module: KNX2Module,
        entity_platform: EntityPlatform,
        entity_class: type[Knx2UiEntity],
    ) -> None:
        """Initialize the UI platform."""
        self._knx2_module = knx2_module
        self._entity_platform = entity_platform
        self._entity_class = entity_class

    async def create_entity(self, unique_id: str, config: dict[str, Any]) -> None:
        """Add a new UI entity."""
        await self._entity_platform.async_add_entities(
            [self._entity_class(self._knx2_module, unique_id, config)]
        )

    async def update_entity(
        self, entity_entry: RegistryEntry, config: dict[str, Any]
    ) -> None:
        """Update an existing UI entities configuration."""
        await self._entity_platform.async_remove_entity(entity_entry.entity_id)
        await self.create_entity(unique_id=entity_entry.unique_id, config=config)


class _Knx2EntityBase(Entity):
    """Representation of a KNX2 entity."""

    _attr_should_poll = False
    _knx2_module: KNX2Module
    _device: XknxDevice

    @property
    def name(self) -> str:
        """Return the name of the KNX2 device."""
        return self._device.name

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._knx2_module.connected

    async def async_update(self) -> None:
        """Request a state update from KNX2 bus."""
        await self._device.sync()

    def after_update_callback(self, _device: XknxDevice) -> None:
        """Call after device was updated."""
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Store register state change callback and start device object."""
        self._device.register_device_updated_cb(self.after_update_callback)
        self._device.xknx.devices.async_add(self._device)
        # super call needed to have methods of multi-inherited classes called
        # eg. for restoring state (like _KNX2Switch)
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Disconnect device object when removed."""
        self._device.unregister_device_updated_cb(self.after_update_callback)
        self._device.xknx.devices.async_remove(self._device)


class Knx2YamlEntity(_Knx2EntityBase):
    """Representation of a KNX2 entity configured from YAML."""

    def __init__(self, knx2_module: KNX2Module, device: XknxDevice) -> None:
        """Initialize the YAML entity."""
        self._knx2_module = knx2_module
        self._device = device


class Knx2UiEntity(_Knx2EntityBase):
    """Representation of a KNX2 UI entity."""

    _attr_unique_id: str
    _attr_has_entity_name = True

    def __init__(
        self, knx2_module: KNX2Module, unique_id: str, entity_config: dict[str, Any]
    ) -> None:
        """Initialize the UI entity."""
        self._knx2_module = knx2_module
        self._attr_unique_id = unique_id
        if entity_category := entity_config.get(CONF_ENTITY_CATEGORY):
            self._attr_entity_category = EntityCategory(entity_category)
        if device_info := entity_config.get(CONF_DEVICE_INFO):
            self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, device_info)})
