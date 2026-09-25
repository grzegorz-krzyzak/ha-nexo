"""Config and options flow for the Nexwell Nexo integration."""

from __future__ import annotations

from collections.abc import Mapping
import copy
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    COVER_CLOSE_COMMAND,
    COVER_DEVICE_CLASS,
    COVER_OPEN_COMMAND,
    COVER_OPEN_ONLY_WHEN_CLOSED,
    COVER_REED_SENSOR,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ITEM_COMMAND,
    ITEM_ID,
    ITEM_NAME,
    MAX_LOGIC_COMMAND,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_BUTTONS,
    OPT_COVERS,
    OPT_SCAN_INTERVAL,
    OPT_THERMOMETERS,
)
from .nexo_client import ImportTypes, NexoAuthError, NexoClient, NexoError

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
            NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)

COVER_DEVICE_CLASSES = ["gate", "garage", "door"]


def _check_login(host: str, port: int, password: str) -> None:
    client = NexoClient(host, password, port=port, retries=0)
    try:
        client.ping()
    finally:
        client.disconnect()


async def _validate(hass: HomeAssistant, host: str, port: int, password: str) -> dict[str, str]:
    try:
        await hass.async_add_executor_job(_check_login, host, port, password)
    except NexoAuthError:
        return {"base": "invalid_auth"}
    except (NexoError, OSError):
        return {"base": "cannot_connect"}
    return {}


def _check_command(value: str) -> str | None:
    if not value:
        return "command_empty"
    if len(value) > MAX_LOGIC_COMMAND:
        return "command_too_long"
    return None


def _pick_many(names: list[str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=names, multiple=True, sort=True, mode=SelectSelectorMode.DROPDOWN
        )
    )


def _pick_one(names: list[str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=names, sort=True, mode=SelectSelectorMode.DROPDOWN)
    )


class NexoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Connect to a central unit through its LAN card."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            await self.async_set_unique_id(host)
            self._abort_if_unique_id_configured()
            errors = await _validate(self.hass, host, port, user_input[CONF_PASSWORD])
            if not errors:
                return self.async_create_entry(
                    title=f"Nexo {host}",
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await _validate(
                self.hass,
                entry.data[CONF_HOST],
                entry.data.get(CONF_PORT, DEFAULT_PORT),
                user_input[CONF_PASSWORD],
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=REAUTH_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> NexoOptionsFlow:
        return NexoOptionsFlow()


class NexoOptionsFlow(OptionsFlow):
    """Choose which resources to import and define logic-driven entities."""

    def _options(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.config_entry.options))

    async def _resources(self, resource_type: ImportTypes) -> list[str]:
        return await self.config_entry.runtime_data.hub.async_resources(resource_type)

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        menu = ["resources", "add_cover", "add_button"]
        options = self.config_entry.options
        if options.get(OPT_COVERS) or options.get(OPT_BUTTONS):
            menu.append("remove")
        menu.append("settings")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_resources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            available = {
                OPT_BINARY_SENSORS: await self._resources(ImportTypes.SENSOR),
                OPT_THERMOMETERS: await self._resources(ImportTypes.THERMOMETER),
                OPT_ANALOG_SENSORS: await self._resources(ImportTypes.ANALOGSENSOR),
            }
        except NexoError:
            return self.async_abort(reason="cannot_list")

        if user_input is not None:
            return self.async_create_entry(data={**self._options(), **user_input})

        options = self.config_entry.options
        schema = vol.Schema(
            {
                # A resource renamed or removed in the central unit drops out
                # of the defaults rather than failing validation.
                vol.Optional(
                    key,
                    default=[n for n in options.get(key, []) if n in names],
                ): _pick_many(names)
                for key, names in available.items()
            }
        )
        return self.async_show_form(step_id="resources", data_schema=schema)

    async def async_step_add_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            sensors = await self._resources(ImportTypes.SENSOR)
        except NexoError:
            return self.async_abort(reason="cannot_list")

        errors: dict[str, str] = {}
        if user_input is not None:
            for key in (COVER_OPEN_COMMAND, COVER_CLOSE_COMMAND):
                user_input[key] = user_input[key].strip()
                if error := _check_command(user_input[key]):
                    errors[key] = error
            if not errors:
                options = self._options()
                options.setdefault(OPT_COVERS, []).append(
                    {ITEM_ID: uuid.uuid4().hex, **user_input}
                )
                return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Required(ITEM_NAME): TextSelector(),
                vol.Required(COVER_DEVICE_CLASS, default="gate"): SelectSelector(
                    SelectSelectorConfig(
                        options=COVER_DEVICE_CLASSES,
                        translation_key="cover_device_class",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(COVER_OPEN_COMMAND): TextSelector(),
                vol.Required(COVER_CLOSE_COMMAND): TextSelector(),
                vol.Required(COVER_REED_SENSOR): _pick_one(sensors),
                vol.Required(COVER_OPEN_ONLY_WHEN_CLOSED, default=False): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="add_cover",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_add_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input[ITEM_COMMAND] = user_input[ITEM_COMMAND].strip()
            if error := _check_command(user_input[ITEM_COMMAND]):
                errors[ITEM_COMMAND] = error
            else:
                options = self._options()
                options.setdefault(OPT_BUTTONS, []).append(
                    {ITEM_ID: uuid.uuid4().hex, **user_input}
                )
                return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Required(ITEM_NAME): TextSelector(),
                vol.Required(ITEM_COMMAND): TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="add_button",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options()
        if user_input is not None:
            doomed = set(user_input["items"])
            for key in (OPT_COVERS, OPT_BUTTONS):
                options[key] = [i for i in options.get(key, []) if i[ITEM_ID] not in doomed]
            return self.async_create_entry(data=options)

        items = [
            {"value": item[ITEM_ID], "label": f"{item[ITEM_NAME]} ({kind})"}
            for key, kind in ((OPT_COVERS, "cover"), (OPT_BUTTONS, "button"))
            for item in options.get(key, [])
        ]
        schema = vol.Schema(
            {
                vol.Required("items", default=[]): SelectSelector(
                    SelectSelectorConfig(
                        options=items, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(step_id="remove", data_schema=schema)

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            options = self._options()
            options[OPT_SCAN_INTERVAL] = int(user_input[OPT_SCAN_INTERVAL])
            return self.async_create_entry(data=options)

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
