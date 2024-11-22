"""KNX2 integration services."""

from __future__ import annotations

from functools import partial
import logging
from typing import TYPE_CHECKING

import voluptuous as vol
from xknx.dpt import DPTArray, DPTBase, DPTBinary
from xknx.exceptions import ConversionError
from xknx.telegram import Telegram
from xknx.telegram.address import parse_device_group_address
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from homeassistant.const import CONF_TYPE, SERVICE_RELOAD
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .const import (
    DOMAIN,
    KNX2_ADDRESS,
    KNX2_MODULE_KEY,
    SERVICE_KNX2_ATTR_PAYLOAD,
    SERVICE_KNX2_ATTR_REMOVE,
    SERVICE_KNX2_ATTR_RESPONSE,
    SERVICE_KNX2_ATTR_TYPE,
    SERVICE_KNX2_EVENT_REGISTER,
    SERVICE_KNX2_EXPOSURE_REGISTER,
    SERVICE_KNX2_READ,
    SERVICE_KNX2_SEND,
)
from .expose import create_knx2_exposure
from .schema import ExposeSchema, dpt_base_type_validator, ga_validator

if TYPE_CHECKING:
    from . import KNX2Module

_LOGGER = logging.getLogger(__name__)


@callback
def register_knx2_services(hass: HomeAssistant) -> None:
    """Register KNX2 integration services."""
    hass.services.async_register(
        DOMAIN,
        SERVICE_KNX2_SEND,
        partial(service_send_to_knx2_bus, hass),
        schema=SERVICE_KNX2_SEND_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_KNX2_READ,
        partial(service_read_to_knx2_bus, hass),
        schema=SERVICE_KNX2_READ_SCHEMA,
    )

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_KNX2_EVENT_REGISTER,
        partial(service_event_register_modify, hass),
        schema=SERVICE_KNX2_EVENT_REGISTER_SCHEMA,
    )

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_KNX2_EXPOSURE_REGISTER,
        partial(service_exposure_register_modify, hass),
        schema=SERVICE_KNX2_EXPOSURE_REGISTER_SCHEMA,
    )

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        partial(service_reload_integration, hass),
    )


@callback
def get_knx2_module(hass: HomeAssistant) -> KNX2Module:
    """Return KNX2Module instance."""
    try:
        return hass.data[KNX2_MODULE_KEY]
    except KeyError as err:
        raise HomeAssistantError("KNX2 entry not loaded") from err


SERVICE_KNX2_EVENT_REGISTER_SCHEMA = vol.Schema(
    {
        vol.Required(KNX2_ADDRESS): vol.All(
            cv.ensure_list,
            [ga_validator],
        ),
        vol.Optional(CONF_TYPE): dpt_base_type_validator,
        vol.Optional(SERVICE_KNX2_ATTR_REMOVE, default=False): cv.boolean,
    }
)


async def service_event_register_modify(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service for adding or removing a GroupAddress to the knx2_event filter."""
    knx2_module = get_knx2_module(hass)

    attr_address = call.data[KNX2_ADDRESS]
    group_addresses = list(map(parse_device_group_address, attr_address))

    if call.data.get(SERVICE_KNX2_ATTR_REMOVE):
        for group_address in group_addresses:
            try:
                knx2_module.knx2_event_callback.group_addresses.remove(group_address)
            except ValueError:
                _LOGGER.warning(
                    "Service event_register could not remove event for '%s'",
                    str(group_address),
                )
            if group_address in knx2_module.group_address_transcoder:
                del knx2_module.group_address_transcoder[group_address]
        return

    if (dpt := call.data.get(CONF_TYPE)) and (
        transcoder := DPTBase.parse_transcoder(dpt)
    ):
        knx2_module.group_address_transcoder.update(
            {_address: transcoder for _address in group_addresses}
        )
    for group_address in group_addresses:
        if group_address in knx2_module.knx2_event_callback.group_addresses:
            continue
        knx2_module.knx2_event_callback.group_addresses.append(group_address)
        _LOGGER.debug(
            "Service event_register registered event for '%s'",
            str(group_address),
        )


SERVICE_KNX2_EXPOSURE_REGISTER_SCHEMA = vol.Any(
    ExposeSchema.EXPOSE_SENSOR_SCHEMA.extend(
        {
            vol.Optional(SERVICE_KNX2_ATTR_REMOVE, default=False): cv.boolean,
        }
    ),
    vol.Schema(
        # for removing only `address` is required
        {
            vol.Required(KNX2_ADDRESS): ga_validator,
            vol.Required(SERVICE_KNX2_ATTR_REMOVE): vol.All(cv.boolean, True),
        },
        extra=vol.ALLOW_EXTRA,
    ),
)


async def service_exposure_register_modify(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Service for adding or removing an exposure to KNX2 bus."""
    knx2_module = get_knx2_module(hass)

    group_address = call.data[KNX2_ADDRESS]

    if call.data.get(SERVICE_KNX2_ATTR_REMOVE):
        try:
            removed_exposure = knx2_module.service_exposures.pop(group_address)
        except KeyError as err:
            raise ServiceValidationError(
                f"Could not find exposure for '{group_address}' to remove."
            ) from err

        removed_exposure.async_remove()
        return

    if group_address in knx2_module.service_exposures:
        replaced_exposure = knx2_module.service_exposures.pop(group_address)
        _LOGGER.warning(
            (
                "Service exposure_register replacing already registered exposure"
                " for '%s' - %s"
            ),
            group_address,
            replaced_exposure.device.name,
        )
        replaced_exposure.async_remove()
    exposure = create_knx2_exposure(knx2_module.hass, knx2_module.xknx, call.data)
    knx2_module.service_exposures[group_address] = exposure
    _LOGGER.debug(
        "Service exposure_register registered exposure for '%s' - %s",
        group_address,
        exposure.device.name,
    )


SERVICE_KNX2_SEND_SCHEMA = vol.Any(
    vol.Schema(
        {
            vol.Required(KNX2_ADDRESS): vol.All(
                cv.ensure_list,
                [ga_validator],
            ),
            vol.Required(SERVICE_KNX2_ATTR_PAYLOAD): cv.match_all,
            vol.Required(SERVICE_KNX2_ATTR_TYPE): dpt_base_type_validator,
            vol.Optional(SERVICE_KNX2_ATTR_RESPONSE, default=False): cv.boolean,
        }
    ),
    vol.Schema(
        # without type given payload is treated as raw bytes
        {
            vol.Required(KNX2_ADDRESS): vol.All(
                cv.ensure_list,
                [ga_validator],
            ),
            vol.Required(SERVICE_KNX2_ATTR_PAYLOAD): vol.Any(
                cv.positive_int, [cv.positive_int]
            ),
            vol.Optional(SERVICE_KNX2_ATTR_RESPONSE, default=False): cv.boolean,
        }
    ),
)


async def service_send_to_knx2_bus(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service for sending an arbitrary KNX2 message to the KNX2 bus."""
    knx2_module = get_knx2_module(hass)

    attr_address = call.data[KNX2_ADDRESS]
    attr_payload = call.data[SERVICE_KNX2_ATTR_PAYLOAD]
    attr_type = call.data.get(SERVICE_KNX2_ATTR_TYPE)
    attr_response = call.data[SERVICE_KNX2_ATTR_RESPONSE]

    payload: DPTBinary | DPTArray
    if attr_type is not None:
        transcoder = DPTBase.parse_transcoder(attr_type)
        if transcoder is None:
            raise ServiceValidationError(
                f"Invalid type for knx2.send service: {attr_type}"
            )
        try:
            payload = transcoder.to_knx2(attr_payload)
        except ConversionError as err:
            raise ServiceValidationError(
                f"Invalid payload for knx2.send service: {err}"
            ) from err
    elif isinstance(attr_payload, int):
        payload = DPTBinary(attr_payload)
    else:
        payload = DPTArray(attr_payload)

    for address in attr_address:
        telegram = Telegram(
            destination_address=parse_device_group_address(address),
            payload=GroupValueResponse(payload)
            if attr_response
            else GroupValueWrite(payload),
            source_address=knx2_module.xknx.current_address,
        )
        await knx2_module.xknx.telegrams.put(telegram)


SERVICE_KNX2_READ_SCHEMA = vol.Schema(
    {
        vol.Required(KNX2_ADDRESS): vol.All(
            cv.ensure_list,
            [ga_validator],
        )
    }
)


async def service_read_to_knx2_bus(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service for sending a GroupValueRead telegram to the KNX2 bus."""
    knx2_module = get_knx2_module(hass)

    for address in call.data[KNX2_ADDRESS]:
        telegram = Telegram(
            destination_address=parse_device_group_address(address),
            payload=GroupValueRead(),
            source_address=knx2_module.xknx.current_address,
        )
        await knx2_module.xknx.telegrams.put(telegram)


async def service_reload_integration(hass: HomeAssistant, call: ServiceCall) -> None:
    """Reload the integration."""
    knx2_module = get_knx2_module(hass)
    await hass.config_entries.async_reload(knx2_module.entry.entry_id)
    hass.bus.async_fire(f"event_{DOMAIN}_reloaded", context=call.context)
