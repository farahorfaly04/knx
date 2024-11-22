"""KNX2 Websocket API."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import TYPE_CHECKING, Any, Final, overload

import knx_frontend as knx_panel
import voluptuous as vol
from xknx.telegram import Telegram
from xknxproject.exceptions import XknxProjectException

from homeassistant.components import panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_ENTITY_ID, CONF_PLATFORM
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import UNDEFINED
from homeassistant.util.ulid import ulid_now

from .const import DOMAIN, KNX2_MODULE_KEY
from .storage.config_store import ConfigStoreException
from .storage.const import CONF_DATA
from .storage.entity_store_schema import (
    CREATE_ENTITY_BASE_SCHEMA,
    UPDATE_ENTITY_BASE_SCHEMA,
)
from .storage.entity_store_validation import (
    EntityStoreValidationException,
    EntityStoreValidationSuccess,
    validate_entity_data,
)
from .telegrams import SIGNAL_KNX2_TELEGRAM, TelegramDict

if TYPE_CHECKING:
    from . import KNX2Module

URL_BASE: Final = "/knx_static"


async def register_panel(hass: HomeAssistant) -> None:
    """Register the KNX2 Panel and Websocket API."""
    websocket_api.async_register_command(hass, ws_info)
    websocket_api.async_register_command(hass, ws_project_file_process)
    websocket_api.async_register_command(hass, ws_project_file_remove)
    websocket_api.async_register_command(hass, ws_group_monitor_info)
    websocket_api.async_register_command(hass, ws_group_telegrams)
    websocket_api.async_register_command(hass, ws_subscribe_telegram)
    websocket_api.async_register_command(hass, ws_get_knx2_project)
    websocket_api.async_register_command(hass, ws_validate_entity)
    websocket_api.async_register_command(hass, ws_create_entity)
    websocket_api.async_register_command(hass, ws_update_entity)
    websocket_api.async_register_command(hass, ws_delete_entity)
    websocket_api.async_register_command(hass, ws_get_entity_config)
    websocket_api.async_register_command(hass, ws_get_entity_entries)
    websocket_api.async_register_command(hass, ws_create_device)

    if DOMAIN not in hass.data.get("frontend_panels", {}):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    URL_BASE,
                    path=knx_panel.locate_dir(),
                    cache_headers=knx_panel.is_prod_build,
                )
            ]
        )
        await panel_custom.async_register_panel(
            hass=hass,
            frontend_url_path=DOMAIN,
            webcomponent_name=knx_panel.webcomponent_name,
            sidebar_title=DOMAIN.upper(),
            sidebar_icon="mdi:bus-electric",
            module_url=f"{URL_BASE}/{knx_panel.entrypoint_js}",
            embed_iframe=True,
            require_admin=True,
        )


type Knx2WebSocketCommandHandler = Callable[
    [HomeAssistant, KNX2Module, websocket_api.ActiveConnection, dict[str, Any]], None
]
type Knx2AsyncWebSocketCommandHandler = Callable[
    [HomeAssistant, KNX2Module, websocket_api.ActiveConnection, dict[str, Any]],
    Awaitable[None],
]


@overload
def provide_knx2(
    func: Knx2AsyncWebSocketCommandHandler,
) -> websocket_api.const.AsyncWebSocketCommandHandler: ...
@overload
def provide_knx2(
    func: Knx2WebSocketCommandHandler,
) -> websocket_api.const.WebSocketCommandHandler: ...


def provide_knx2(
    func: Knx2AsyncWebSocketCommandHandler | Knx2WebSocketCommandHandler,
) -> (
    websocket_api.const.AsyncWebSocketCommandHandler
    | websocket_api.const.WebSocketCommandHandler
):
    """Websocket decorator to provide a KNX2Module instance."""

    def _send_not_loaded_error(
        connection: websocket_api.ActiveConnection, msg_id: int
    ) -> None:
        connection.send_error(
            msg_id,
            websocket_api.const.ERR_HOME_ASSISTANT_ERROR,
            "KNX2 integration not loaded.",
        )

    if asyncio.iscoroutinefunction(func):

        @wraps(func)
        async def with_knx2(
            hass: HomeAssistant,
            connection: websocket_api.ActiveConnection,
            msg: dict[str, Any],
        ) -> None:
            """Add KNX2 Module to call function."""
            try:
                knx2 = hass.data[KNX2_MODULE_KEY]
            except KeyError:
                _send_not_loaded_error(connection, msg["id"])
                return
            await func(hass, knx2, connection, msg)

    else:

        @wraps(func)
        def with_knx2(
            hass: HomeAssistant,
            connection: websocket_api.ActiveConnection,
            msg: dict[str, Any],
        ) -> None:
            """Add KNX2 Module to call function."""
            try:
                knx2 = hass.data[KNX2_MODULE_KEY]
            except KeyError:
                _send_not_loaded_error(connection, msg["id"])
                return
            func(hass, knx2, connection, msg)

    return with_knx2


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/info",
    }
)
@provide_knx2
@callback
def ws_info(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle get info command."""
    _project_info = None
    if project_info := knx2.project.info:
        _project_info = {
            "name": project_info["name"],
            "last_modified": project_info["last_modified"],
            "tool_version": project_info["tool_version"],
            "xknxproject_version": project_info["xknxproject_version"],
        }

    connection.send_result(
        msg["id"],
        {
            "version": knx2.xknx.version,
            "connected": knx2.xknx.connection_manager.connected.is_set(),
            "current_address": str(knx2.xknx.current_address),
            "project": _project_info,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/get_knx2_project",
    }
)
@websocket_api.async_response
@provide_knx2
async def ws_get_knx2_project(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle get KNX2 project."""
    knx2project = await knx2.project.get_knx2project()
    connection.send_result(
        msg["id"],
        {
            "project_loaded": knx2.project.loaded,
            "knx2project": knx2project,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/project_file_process",
        vol.Required("file_id"): str,
        vol.Required("password"): str,
    }
)
@websocket_api.async_response
@provide_knx2
async def ws_project_file_process(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle get info command."""
    try:
        await knx2.project.process_project_file(
            xknx=knx2.xknx,
            file_id=msg["file_id"],
            password=msg["password"],
        )
    except (ValueError, XknxProjectException) as err:
        # ValueError could raise from file_upload integration
        connection.send_error(
            msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR, str(err)
        )
        return

    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/project_file_remove",
    }
)
@websocket_api.async_response
@provide_knx2
async def ws_project_file_remove(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle get info command."""
    await knx2.project.remove_project_file()
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/group_monitor_info",
    }
)
@provide_knx2
@callback
def ws_group_monitor_info(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle get info command of group monitor."""
    recent_telegrams = [*knx2.telegrams.recent_telegrams]
    connection.send_result(
        msg["id"],
        {
            "project_loaded": knx2.project.loaded,
            "recent_telegrams": recent_telegrams,
        },
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/group_telegrams",
    }
)
@provide_knx2
@callback
def ws_group_telegrams(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Handle get group telegrams command."""
    connection.send_result(
        msg["id"],
        knx2.telegrams.last_ga_telegrams,
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/subscribe_telegrams",
    }
)
@callback
def ws_subscribe_telegram(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Subscribe to incoming and outgoing KNX2 telegrams."""

    @callback
    def forward_telegram(_telegram: Telegram, telegram_dict: TelegramDict) -> None:
        """Forward telegram to websocket subscription."""
        connection.send_event(
            msg["id"],
            telegram_dict,
        )

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass,
        signal=SIGNAL_KNX2_TELEGRAM,
        target=forward_telegram,
    )
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/validate_entity",
        **CREATE_ENTITY_BASE_SCHEMA,
    }
)
@callback
def ws_validate_entity(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Validate entity data."""
    try:
        validate_entity_data(msg)
    except EntityStoreValidationException as exc:
        connection.send_result(msg["id"], exc.validation_error)
        return
    connection.send_result(
        msg["id"], EntityStoreValidationSuccess(success=True, entity_id=None)
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/create_entity",
        **CREATE_ENTITY_BASE_SCHEMA,
    }
)
@websocket_api.async_response
@provide_knx2
async def ws_create_entity(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Create entity in entity store and load it."""
    try:
        validated_data = validate_entity_data(msg)
    except EntityStoreValidationException as exc:
        connection.send_result(msg["id"], exc.validation_error)
        return
    try:
        entity_id = await knx2.config_store.create_entity(
            # use validation result so defaults are applied
            validated_data[CONF_PLATFORM],
            validated_data[CONF_DATA],
        )
    except ConfigStoreException as err:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_HOME_ASSISTANT_ERROR, str(err)
        )
        return
    connection.send_result(
        msg["id"], EntityStoreValidationSuccess(success=True, entity_id=entity_id)
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/update_entity",
        **UPDATE_ENTITY_BASE_SCHEMA,
    }
)
@websocket_api.async_response
@provide_knx2
async def ws_update_entity(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Update entity in entity store and reload it."""
    try:
        validated_data = validate_entity_data(msg)
    except EntityStoreValidationException as exc:
        connection.send_result(msg["id"], exc.validation_error)
        return
    try:
        await knx2.config_store.update_entity(
            validated_data[CONF_PLATFORM],
            validated_data[CONF_ENTITY_ID],
            validated_data[CONF_DATA],
        )
    except ConfigStoreException as err:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_HOME_ASSISTANT_ERROR, str(err)
        )
        return
    connection.send_result(
        msg["id"], EntityStoreValidationSuccess(success=True, entity_id=None)
    )


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/delete_entity",
        vol.Required(CONF_ENTITY_ID): str,
    }
)
@websocket_api.async_response
@provide_knx2
async def ws_delete_entity(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Delete entity from entity store and remove it."""
    try:
        await knx2.config_store.delete_entity(msg[CONF_ENTITY_ID])
    except ConfigStoreException as err:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_HOME_ASSISTANT_ERROR, str(err)
        )
        return
    connection.send_result(msg["id"])


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/get_entity_entries",
    }
)
@provide_knx2
@callback
def ws_get_entity_entries(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Get entities configured from entity store."""
    entity_entries = [
        entry.extended_dict for entry in knx2.config_store.get_entity_entries()
    ]
    connection.send_result(msg["id"], entity_entries)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/get_entity_config",
        vol.Required(CONF_ENTITY_ID): str,
    }
)
@provide_knx2
@callback
def ws_get_entity_config(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Get entity configuration from entity store."""
    try:
        config_info = knx2.config_store.get_entity_config(msg[CONF_ENTITY_ID])
    except ConfigStoreException as err:
        connection.send_error(
            msg["id"], websocket_api.const.ERR_HOME_ASSISTANT_ERROR, str(err)
        )
        return
    connection.send_result(msg["id"], config_info)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "knx2/create_device",
        vol.Required("name"): str,
        vol.Optional("area_id"): str,
    }
)
@provide_knx2
@callback
def ws_create_device(
    hass: HomeAssistant,
    knx2: KNX2Module,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Create a new KNX2 device."""
    identifier = f"knx2_vdev_{ulid_now()}"
    device_registry = dr.async_get(hass)
    _device = device_registry.async_get_or_create(
        config_entry_id=knx2.entry.entry_id,
        manufacturer="KNX2",
        name=msg["name"],
        identifiers={(DOMAIN, identifier)},
    )
    device_registry.async_update_device(
        _device.id,
        area_id=msg.get("area_id") or UNDEFINED,
        configuration_url=f"homeassistant://knx2/entities/view?device_id={_device.id}",
    )
    connection.send_result(msg["id"], _device.dict_repr)
