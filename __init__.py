"""Support KNX2 devices."""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path
from typing import Final

import voluptuous as vol
from xknx import XKNX
from xknx.core import XknxConnectionState
from xknx.core.telegram_queue import TelegramQueue
from xknx.dpt import DPTBase
from xknx.exceptions import ConversionError, CouldNotParseTelegram, XKNXException
from xknx.io import ConnectionConfig, ConnectionType, SecureConfig
from xknx.telegram import AddressFilter, Telegram
from xknx.telegram.address import DeviceGroupAddress, GroupAddress, InternalGroupAddress
from xknx.telegram.apci import GroupValueResponse, GroupValueWrite

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_EVENT,
    CONF_HOST,
    CONF_PORT,
    CONF_TYPE,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.reload import async_integration_yaml_config
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_KNX2_CONNECTION_TYPE,
    CONF_KNX2_EXPOSE,
    CONF_KNX2_INDIVIDUAL_ADDRESS,
    CONF_KNX2_KNX2KEY_FILENAME,
    CONF_KNX2_KNX2KEY_PASSWORD,
    CONF_KNX2_LOCAL_IP,
    CONF_KNX2_MCAST_GRP,
    CONF_KNX2_MCAST_PORT,
    CONF_KNX2_RATE_LIMIT,
    CONF_KNX2_ROUTE_BACK,
    CONF_KNX2_ROUTING,
    CONF_KNX2_ROUTING_BACKBONE_KEY,
    CONF_KNX2_ROUTING_SECURE,
    CONF_KNX2_ROUTING_SYNC_LATENCY_TOLERANCE,
    CONF_KNX2_SECURE_DEVICE_AUTHENTICATION,
    CONF_KNX2_SECURE_USER_ID,
    CONF_KNX2_SECURE_USER_PASSWORD,
    CONF_KNX2_STATE_UPDATER,
    CONF_KNX2_TELEGRAM_LOG_SIZE,
    CONF_KNX2_TUNNELING,
    CONF_KNX2_TUNNELING_TCP,
    CONF_KNX2_TUNNELING_TCP_SECURE,
    DATA_HASS_CONFIG,
    DOMAIN,
    KNX2_ADDRESS,
    KNX2_MODULE_KEY,
    SUPPORTED_PLATFORMS_UI,
    SUPPORTED_PLATFORMS_YAML,
    TELEGRAM_LOG_DEFAULT,
)
from .device import KNX2InterfaceDevice
from .expose import KNX2ExposeSensor, KNX2ExposeTime, create_knx_exposure
from .project import STORAGE_KEY as PROJECT_STORAGE_KEY, KNX2Project
from .schema import (
    BinarySensorSchema,
    ButtonSchema,
    ClimateSchema,
    CoverSchema,
    DateSchema,
    DateTimeSchema,
    EventSchema,
    ExposeSchema,
    FanSchema,
    LightSchema,
    NotifySchema,
    NumberSchema,
    SceneSchema,
    SelectSchema,
    SensorSchema,
    SwitchSchema,
    TextSchema,
    TimeSchema,
    WeatherSchema,
)
from .services import register_knx_services
from .storage.config_store import KNX2ConfigStore
from .telegrams import STORAGE_KEY as TELEGRAMS_STORAGE_KEY, Telegrams
from .websocket import register_panel

_LOGGER = logging.getLogger(__name__)

_KNX2_YAML_CONFIG: Final = "knx2_yaml_config"

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            # deprecated since 2021.12
            cv.deprecated(CONF_KNX2_STATE_UPDATER),
            cv.deprecated(CONF_KNX2_RATE_LIMIT),
            cv.deprecated(CONF_KNX2_ROUTING),
            cv.deprecated(CONF_KNX2_TUNNELING),
            cv.deprecated(CONF_KNX2_INDIVIDUAL_ADDRESS),
            cv.deprecated(CONF_KNX2_MCAST_GRP),
            cv.deprecated(CONF_KNX2_MCAST_PORT),
            cv.deprecated("event_filter"),
            # deprecated since 2021.4
            cv.deprecated("config_file"),
            # deprecated since 2021.2
            cv.deprecated("fire_event"),
            cv.deprecated("fire_event_filter"),
            vol.Schema(
                {
                    **EventSchema.SCHEMA,
                    **ExposeSchema.platform_node(),
                    **BinarySensorSchema.platform_node(),
                    **ButtonSchema.platform_node(),
                    **ClimateSchema.platform_node(),
                    **CoverSchema.platform_node(),
                    **DateSchema.platform_node(),
                    **DateTimeSchema.platform_node(),
                    **FanSchema.platform_node(),
                    **LightSchema.platform_node(),
                    **NotifySchema.platform_node(),
                    **NumberSchema.platform_node(),
                    **SceneSchema.platform_node(),
                    **SelectSchema.platform_node(),
                    **SensorSchema.platform_node(),
                    **SwitchSchema.platform_node(),
                    **TextSchema.platform_node(),
                    **TimeSchema.platform_node(),
                    **WeatherSchema.platform_node(),
                }
            ),
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Start the KNX2 integration."""
    hass.data[DATA_HASS_CONFIG] = config
    if (conf := config.get(DOMAIN)) is not None:
        hass.data[_KNX2_YAML_CONFIG] = dict(conf)

    register_knx2_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load a config entry."""
    # `_KNX2_YAML_CONFIG` is only set in async_setup.
    # It's None when reloading the integration or no `knx2` key in configuration.yaml
    config = hass.data.pop(_KNX2_YAML_CONFIG, None)
    if config is None:
        _conf = await async_integration_yaml_config(hass, DOMAIN)
        if not _conf or DOMAIN not in _conf:
            # generate defaults
            config = CONFIG_SCHEMA({DOMAIN: {}})[DOMAIN]
        else:
            config = _conf[DOMAIN]
    try:
        knx2_module = KNX2Module(hass, config, entry)
        await knx2_module.start()
    except XKNXException as ex:
        raise ConfigEntryNotReady from ex

    hass.data[KNX2_MODULE_KEY] = knx2_module

    if CONF_KNX2_EXPOSE in config:
        for expose_config in config[CONF_KNX2_EXPOSE]:
            knx2_module.exposures.append(
                create_knx_exposure(hass, knx2_module.xknx, expose_config)
            )
    configured_platforms_yaml = {
        platform for platform in SUPPORTED_PLATFORMS_YAML if platform in config
    }
    await hass.config_entries.async_forward_entry_setups(
        entry,
        {
            Platform.SENSOR,  # always forward sensor for system entities (telegram counter, etc.)
            *SUPPORTED_PLATFORMS_UI,  # forward all platforms that support UI entity management
            *configured_platforms_yaml,  # forward yaml-only managed platforms on demand,
        },
    )

    await register_panel(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unloading the KNX2 platforms."""
    knx2_module = hass.data.get(KNX2_MODULE_KEY)
    if not knx2_module:
        #  if not loaded directly return
        return True

    for exposure in knx2_module.exposures:
        exposure.async_remove()

    configured_platforms_yaml = {
        platform
        for platform in SUPPORTED_PLATFORMS_YAML
        if platform in knx2_module.config_yaml
    }
    unload_ok = await hass.config_entries.async_unload_platforms(
        entry,
        {
            Platform.SENSOR,  # always unload system entities (telegram counter, etc.)
            *SUPPORTED_PLATFORMS_UI,  # unload all platforms that support UI entity management
            *configured_platforms_yaml,  # unload yaml-only managed platforms if configured,
        },
    )
    if unload_ok:
        await knx2_module.stop()
        hass.data.pop(DOMAIN)

    return unload_ok


async def async_update_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Update a given config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a config entry."""

    def remove_files(storage_dir: Path, knx2keys_filename: str | None) -> None:
        """Remove KNX2 files."""
        if knx2keys_filename is not None:
            with contextlib.suppress(FileNotFoundError):
                (storage_dir / knx2keys_filename).unlink()
        with contextlib.suppress(FileNotFoundError):
            (storage_dir / PROJECT_STORAGE_KEY).unlink()
        with contextlib.suppress(FileNotFoundError):
            (storage_dir / TELEGRAMS_STORAGE_KEY).unlink()
        with contextlib.suppress(FileNotFoundError, OSError):
            (storage_dir / DOMAIN).rmdir()

    storage_dir = Path(hass.config.path(STORAGE_DIR))
    knx2keys_filename = entry.data.get(CONF_KNX2_KNX2KEY_FILENAME)
    await hass.async_add_executor_job(remove_files, storage_dir, knx2keys_filename)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Remove a config entry from a device."""
    knx2_module = hass.data[KNX2_MODULE_KEY]
    if not device_entry.identifiers.isdisjoint(
        knx2_module.interface_device.device_info["identifiers"]
    ):
        # can not remove interface device
        return False
    for entity in knx2_module.config_store.get_entity_entries():
        if entity.device_id == device_entry.id:
            await knx2_module.config_store.delete_entity(entity.entity_id)
    return True


class KNX2Module:
    """Representation of KNX2 Object."""

    def __init__(
        self, hass: HomeAssistant, config: ConfigType, entry: ConfigEntry
    ) -> None:
        """Initialize KNX2 module."""
        self.hass = hass
        self.config_yaml = config
        self.connected = False
        self.exposures: list[KNX2ExposeSensor | KNX2ExposeTime] = []
        self.service_exposures: dict[str, KNX2ExposeSensor | KNX2ExposeTime] = {}
        self.entry = entry

        self.project = KNX2Project(hass=hass, entry=entry)
        self.config_store = KNX2ConfigStore(hass=hass, config_entry=entry)

        self.xknx = XKNX(
            address_format=self.project.get_address_format(),
            connection_config=self.connection_config(),
            rate_limit=self.entry.data[CONF_KNX2_RATE_LIMIT],
            state_updater=self.entry.data[CONF_KNX2_STATE_UPDATER],
        )
        self.xknx.connection_manager.register_connection_state_changed_cb(
            self.connection_state_changed_cb
        )
        self.telegrams = Telegrams(
            hass=hass,
            xknx=self.xknx,
            project=self.project,
            log_size=entry.data.get(CONF_KNX2_TELEGRAM_LOG_SIZE, TELEGRAM_LOG_DEFAULT),
        )
        self.interface_device = KNX2InterfaceDevice(
            hass=hass, entry=entry, xknx=self.xknx
        )

        self._address_filter_transcoder: dict[AddressFilter, type[DPTBase]] = {}
        self.group_address_transcoder: dict[DeviceGroupAddress, type[DPTBase]] = {}
        self.knx2_event_callback: TelegramQueue.Callback = self.register_event_callback()

        self.entry.async_on_unload(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self.stop)
        )
        self.entry.async_on_unload(self.entry.add_update_listener(async_update_entry))

    async def start(self) -> None:
        """Start XKNX object. Connect to tunneling or Routing device."""
        await self.project.load_project(self.xknx)
        await self.config_store.load_data()
        await self.telegrams.load_history()
        await self.xknx.start()

    async def stop(self, event: Event | None = None) -> None:
        """Stop XKNX object. Disconnect from tunneling or Routing device."""
        await self.xknx.stop()
        await self.telegrams.save_history()

    def connection_config(self) -> ConnectionConfig:
        """Return the connection_config."""
        _conn_type: str = self.entry.data[CONF_KNX2_CONNECTION_TYPE]
        _knx2keys_file: str | None = (
            self.hass.config.path(
                STORAGE_DIR,
                self.entry.data[CONF_KNX2_KNX2KEY_FILENAME],
            )
            if self.entry.data.get(CONF_KNX2_KNX2KEY_FILENAME) is not None
            else None
        )
        if _conn_type == CONF_KNX2_ROUTING:
            return ConnectionConfig(
                connection_type=ConnectionType.ROUTING,
                individual_address=self.entry.data[CONF_KNX2_INDIVIDUAL_ADDRESS],
                multicast_group=self.entry.data[CONF_KNX2_MCAST_GRP],
                multicast_port=self.entry.data[CONF_KNX2_MCAST_PORT],
                local_ip=self.entry.data.get(CONF_KNX2_LOCAL_IP),
                auto_reconnect=True,
                secure_config=SecureConfig(
                    knx2keys_password=self.entry.data.get(CONF_KNX2_KNX2KEY_PASSWORD),
                    knx2keys_file_path=_knx2keys_file,
                ),
                threaded=True,
            )
        if _conn_type == CONF_KNX2_TUNNELING:
            return ConnectionConfig(
                connection_type=ConnectionType.TUNNELING,
                gateway_ip=self.entry.data[CONF_HOST],
                gateway_port=self.entry.data[CONF_PORT],
                local_ip=self.entry.data.get(CONF_KNX2_LOCAL_IP),
                route_back=self.entry.data.get(CONF_KNX2_ROUTE_BACK, False),
                auto_reconnect=True,
                secure_config=SecureConfig(
                    knx2keys_password=self.entry.data.get(CONF_KNX2_KNX2KEY_PASSWORD),
                    knx2keys_file_path=_knx2keys_file,
                ),
                threaded=True,
            )
        if _conn_type == CONF_KNX2_TUNNELING_TCP:
            return ConnectionConfig(
                connection_type=ConnectionType.TUNNELING_TCP,
                gateway_ip=self.entry.data[CONF_HOST],
                gateway_port=self.entry.data[CONF_PORT],
                auto_reconnect=True,
                secure_config=SecureConfig(
                    knx2keys_password=self.entry.data.get(CONF_KNX2_KNX2KEY_PASSWORD),
                    knx2keys_file_path=_knx2keys_file,
                ),
                threaded=True,
            )
        if _conn_type == CONF_KNX2_TUNNELING_TCP_SECURE:
            return ConnectionConfig(
                connection_type=ConnectionType.TUNNELING_TCP_SECURE,
                gateway_ip=self.entry.data[CONF_HOST],
                gateway_port=self.entry.data[CONF_PORT],
                secure_config=SecureConfig(
                    user_id=self.entry.data.get(CONF_KNX2_SECURE_USER_ID),
                    user_password=self.entry.data.get(CONF_KNX2_SECURE_USER_PASSWORD),
                    device_authentication_password=self.entry.data.get(
                        CONF_KNX2_SECURE_DEVICE_AUTHENTICATION
                    ),
                    knx2keys_password=self.entry.data.get(CONF_KNX2_KNX2KEY_PASSWORD),
                    knx2keys_file_path=_knx2keys_file,
                ),
                auto_reconnect=True,
                threaded=True,
            )
        if _conn_type == CONF_KNX2_ROUTING_SECURE:
            return ConnectionConfig(
                connection_type=ConnectionType.ROUTING_SECURE,
                individual_address=self.entry.data[CONF_KNX2_INDIVIDUAL_ADDRESS],
                multicast_group=self.entry.data[CONF_KNX2_MCAST_GRP],
                multicast_port=self.entry.data[CONF_KNX2_MCAST_PORT],
                local_ip=self.entry.data.get(CONF_KNX2_LOCAL_IP),
                secure_config=SecureConfig(
                    backbone_key=self.entry.data.get(CONF_KNX2_ROUTING_BACKBONE_KEY),
                    latency_ms=self.entry.data.get(
                        CONF_KNX2_ROUTING_SYNC_LATENCY_TOLERANCE
                    ),
                    knx2keys_password=self.entry.data.get(CONF_KNX2_KNX2KEY_PASSWORD),
                    knx2keys_file_path=_knx2keys_file,
                ),
                auto_reconnect=True,
                threaded=True,
            )
        return ConnectionConfig(
            auto_reconnect=True,
            secure_config=SecureConfig(
                knx2keys_password=self.entry.data.get(CONF_KNX2_KNX2KEY_PASSWORD),
                knx2keys_file_path=_knx2keys_file,
            ),
            threaded=True,
        )

    def connection_state_changed_cb(self, state: XknxConnectionState) -> None:
        """Call invoked after a KNX2 connection state change was received."""
        self.connected = state == XknxConnectionState.CONNECTED
        for device in self.xknx.devices:
            device.after_update()

    def telegram_received_cb(self, telegram: Telegram) -> None:
        """Call invoked after a KNX2 telegram was received."""
        # Not all telegrams have serializable data.
        data: int | tuple[int, ...] | None = None
        value = None
        if (
            isinstance(telegram.payload, (GroupValueWrite, GroupValueResponse))
            and telegram.payload.value is not None
            and isinstance(
                telegram.destination_address, (GroupAddress, InternalGroupAddress)
            )
        ):
            data = telegram.payload.value.value
            if transcoder := (
                self.group_address_transcoder.get(telegram.destination_address)
                or next(
                    (
                        _transcoder
                        for _filter, _transcoder in self._address_filter_transcoder.items()
                        if _filter.match(telegram.destination_address)
                    ),
                    None,
                )
            ):
                try:
                    value = transcoder.from_knx(telegram.payload.value)
                except (ConversionError, CouldNotParseTelegram) as err:
                    _LOGGER.warning(
                        (
                            "Error in `knx2_event` at decoding type '%s' from"
                            " telegram %s\n%s"
                        ),
                        transcoder.__name__,
                        telegram,
                        err,
                    )

        self.hass.bus.async_fire(
            "knx2_event",
            {
                "data": data,
                "destination": str(telegram.destination_address),
                "direction": telegram.direction.value,
                "value": value,
                "source": str(telegram.source_address),
                "telegramtype": telegram.payload.__class__.__name__,
            },
        )

    def register_event_callback(self) -> TelegramQueue.Callback:
        """Register callback for knx2_event within XKNX TelegramQueue."""
        address_filters = []
        for filter_set in self.config_yaml[CONF_EVENT]:
            _filters = list(map(AddressFilter, filter_set[KNX2_ADDRESS]))
            address_filters.extend(_filters)
            if (dpt := filter_set.get(CONF_TYPE)) and (
                transcoder := DPTBase.parse_transcoder(dpt)
            ):
                self._address_filter_transcoder.update(
                    {_filter: transcoder for _filter in _filters}
                )

        return self.xknx.telegram_queue.register_telegram_received_cb(
            self.telegram_received_cb,
            address_filters=address_filters,
            group_addresses=[],
            match_for_outgoing=True,
        )
