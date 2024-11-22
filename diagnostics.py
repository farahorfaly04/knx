"""Diagnostics support for KNX2."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config as conf_util
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import CONFIG_SCHEMA
from .const import (
    CONF_KNX2_KNX2KEY_PASSWORD,
    CONF_KNX2_ROUTING_BACKBONE_KEY,
    CONF_KNX2_SECURE_DEVICE_AUTHENTICATION,
    CONF_KNX2_SECURE_USER_PASSWORD,
    DOMAIN,
    KNX2_MODULE_KEY,
)

TO_REDACT = {
    CONF_KNX2_ROUTING_BACKBONE_KEY,
    CONF_KNX2_KNX2KEY_PASSWORD,
    CONF_KNX2_SECURE_USER_PASSWORD,
    CONF_KNX2_SECURE_DEVICE_AUTHENTICATION,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    diag: dict[str, Any] = {}
    knx2_module = hass.data[KNX2_MODULE_KEY]
    diag["xknx"] = {
        "version": knx2_module.xknx.version,
        "current_address": str(knx2_module.xknx.current_address),
    }

    diag["config_entry_data"] = async_redact_data(dict(config_entry.data), TO_REDACT)

    if proj_info := knx2_module.project.info:
        diag["project_info"] = async_redact_data(proj_info, "name")
    else:
        diag["project_info"] = None

    raw_config = await conf_util.async_hass_config_yaml(hass)
    diag["configuration_yaml"] = raw_config.get(DOMAIN)
    try:
        CONFIG_SCHEMA(raw_config)
    except vol.Invalid as ex:
        diag["configuration_error"] = str(ex)
    else:
        diag["configuration_error"] = None

    return diag
