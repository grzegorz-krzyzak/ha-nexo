"""Config and options flow for the Nexwell Nexo integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Mapping
import copy
import logging
from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
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
    MAX_ITEMS,
    MAX_LOGIC_COMMAND,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
    OPT_ANALOG_SENSORS,
    OPT_BINARY_SENSORS,
    OPT_BUTTONS,
    OPT_COVERS,
    OPT_SCAN_INTERVAL,
    OPT_THERMOMETERS,
    entry_title,
    is_default_title,
)
from .nexo_client import ImportTypes, NexoAuthError, NexoClient, NexoError

_LOGGER = logging.getLogger(__name__)

PORT_SELECTOR = NumberSelector(
    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
)
PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): PORT_SELECTOR,
        vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)

# The password may be left empty to keep the stored one.
CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): PORT_SELECTOR,
        vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})

COVER_DEVICE_CLASSES = ["gate", "garage", "door"]

# The frontend formats translations as ICU messages, where <...> is a tag
# without attributes - so an ha-alert written into a translation fails with
# INVALID_TAG. Its opening and closing tags are passed in as placeholder
# values instead, which are not parsed. The words stay in the translation and
# pick the state with ICU "select", so they follow the user's language.
NONE = "__none__"


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


def _connection_from_input(entry: ConfigEntry, user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_HOST: user_input[CONF_HOST].strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_PASSWORD: user_input.get(CONF_PASSWORD) or entry.data[CONF_PASSWORD],
    }


def _host_taken(hass: HomeAssistant, entry: ConfigEntry, host: str) -> bool:
    return any(
        other.unique_id == host and other.entry_id != entry.entry_id
        for other in hass.config_entries.async_entries(DOMAIN)
    )


def _title_for(entry: ConfigEntry, connection: dict[str, Any]) -> str:
    """Follow the address in the title, unless the user renamed the entry."""
    old_host = entry.data[CONF_HOST]
    old_port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    if is_default_title(entry.title, old_host, old_port):
        return entry_title(connection[CONF_HOST], connection[CONF_PORT])
    return entry.title


def _is_empty(user_input: dict[str, Any], *keys: str) -> bool:
    return not any(str(user_input.get(key, "")).strip() for key in keys)


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


def _cover_summary(item: dict[str, Any]) -> str:
    parts = [f"{item[COVER_OPEN_COMMAND]} / {item[COVER_CLOSE_COMMAND]}"]
    if item.get(COVER_REED_SENSOR):
        parts.append(item[COVER_REED_SENSOR])
    return " · ".join(parts)


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
                    title=entry_title(host, port),
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            connection = _connection_from_input(entry, user_input)
            host = connection[CONF_HOST]
            if _host_taken(self.hass, entry, host):
                return self.async_abort(reason="already_configured")
            errors = await _validate(
                self.hass, host, connection[CONF_PORT], connection[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=host,
                    title=_title_for(entry, connection),
                    data_updates=connection,
                )
        suggested = user_input or {
            CONF_HOST: entry.data[CONF_HOST],
            CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
        }
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, suggested),
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
    def async_get_options_flow(config_entry: ConfigEntry) -> NexoOptionsFlow:
        return NexoOptionsFlow()


class NexoOptionsFlow(OptionsFlowWithReload):
    """Everything configurable after setup, as a menu.

    Every step returns to a menu, and nothing is stored until "Save and
    close" - closing the dialog discards the changes. Home Assistant forms
    have no back button, so menus carry a "Back" entry instead.

    Each gate and button gets its own menu entry, which needs a step per
    entry: cover_0 ... cover_19 and button_0 ... button_19 are generated
    below the class.
    """

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._connection: dict[str, Any] | None = None

    async def _resources(self, resource_type: ImportTypes) -> list[str]:
        return await self.config_entry.runtime_data.hub.async_resources(resource_type)

    def _cannot_list(self, err: NexoError) -> ConfigFlowResult:
        _LOGGER.warning("Cannot read the resource list from the central unit: %s", err)
        return self.async_abort(reason="cannot_list")

    # ------------------------------------------------------------------ menus

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        self._options = copy.deepcopy(dict(self.config_entry.options))
        return await self.async_step_menu()

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        options = self._options
        connection = self._connection or self.config_entry.data
        # Shown in an ha-alert above the menu, which brings Home Assistant's
        # own icon and colour for each type.
        if self._connection is not None:
            alert, status = "info", "unsaved"
        elif self.config_entry.runtime_data.coordinator.last_update_success:
            alert, status = "success", "connected"
        else:
            alert, status = "warning", "not_answering"
        return self.async_show_menu(
            step_id="menu",
            menu_options=["connection", "sensors", "covers", "buttons", "settings", "save"],
            description_placeholders={
                "address": f"{connection[CONF_HOST]}:{connection.get(CONF_PORT, DEFAULT_PORT)}",
                "alert_open": f'<ha-alert alert-type="{alert}">',
                "alert_close": "</ha-alert>",
                "status": status,
                OPT_BINARY_SENSORS: str(len(options.get(OPT_BINARY_SENSORS, []))),
                OPT_THERMOMETERS: str(len(options.get(OPT_THERMOMETERS, []))),
                OPT_ANALOG_SENSORS: str(len(options.get(OPT_ANALOG_SENSORS, []))),
                OPT_COVERS: ", ".join(i[ITEM_NAME] for i in options.get(OPT_COVERS, []))
                or NONE,
                OPT_BUTTONS: ", ".join(i[ITEM_NAME] for i in options.get(OPT_BUTTONS, []))
                or NONE,
                OPT_SCAN_INTERVAL: str(
                    options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ),
            },
        )

    async def async_step_back(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self.async_step_menu()

    async def async_step_covers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        covers = self._options.get(OPT_COVERS, [])
        menu = [f"cover_{i}" for i in range(len(covers))]
        if len(covers) < MAX_ITEMS:
            menu.append("add_cover")
        menu.append("back")
        placeholders: dict[str, str] = {}
        for i, item in enumerate(covers):
            placeholders[f"cover_{i}"] = item[ITEM_NAME]
            placeholders[f"cover_{i}_info"] = _cover_summary(item)
            placeholders[f"cover_{i}_guard"] = (
                "yes" if item[COVER_OPEN_ONLY_WHEN_CLOSED] else "no"
            )
        return self.async_show_menu(
            step_id="covers", menu_options=menu, description_placeholders=placeholders
        )

    async def async_step_buttons(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        buttons = self._options.get(OPT_BUTTONS, [])
        menu = [f"button_{i}" for i in range(len(buttons))]
        if len(buttons) < MAX_ITEMS:
            menu.append("add_button")
        menu.append("back")
        placeholders: dict[str, str] = {}
        for i, item in enumerate(buttons):
            placeholders[f"button_{i}"] = item[ITEM_NAME]
            placeholders[f"button_{i}_info"] = item[ITEM_COMMAND]
        return self.async_show_menu(
            step_id="buttons", menu_options=menu, description_placeholders=placeholders
        )

    async def async_step_save(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._connection is not None:
            # Connection settings live in the entry's data, not its options.
            # Store both at once and reload here; the options then compare
            # equal, so the automatic reload does not run a second time.
            entry = self.config_entry
            self.hass.config_entries.async_update_entry(
                entry,
                unique_id=self._connection[CONF_HOST],
                title=_title_for(entry, self._connection),
                data={**entry.data, **self._connection},
                options=self._options,
            )
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
        return self.async_create_entry(data=self._options)

    # ------------------------------------------------------------ connection

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            connection = _connection_from_input(entry, user_input)
            if all(entry.data.get(k) == v for k, v in connection.items()):
                # Nothing changed: back to the menu, dropping any unsaved edit
                self._connection = None
                return await self.async_step_menu()
            if _host_taken(self.hass, entry, connection[CONF_HOST]):
                errors["base"] = "already_configured"
            else:
                errors = await _validate(
                    self.hass,
                    connection[CONF_HOST],
                    connection[CONF_PORT],
                    connection[CONF_PASSWORD],
                )
            if not errors:
                self._connection = connection
                return await self.async_step_menu()

        current = self._connection or entry.data
        suggested = user_input or {
            CONF_HOST: current[CONF_HOST],
            CONF_PORT: current.get(CONF_PORT, DEFAULT_PORT),
        }
        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, suggested),
            errors=errors,
        )

    # --------------------------------------------------------------- sensors

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            available = {
                OPT_BINARY_SENSORS: await self._resources(ImportTypes.SENSOR),
                OPT_THERMOMETERS: await self._resources(ImportTypes.THERMOMETER),
                OPT_ANALOG_SENSORS: await self._resources(ImportTypes.ANALOGSENSOR),
            }
        except NexoError as err:
            return self._cannot_list(err)

        if user_input is not None:
            self._options.update(user_input)
            return await self.async_step_menu()

        schema = vol.Schema(
            {
                # A resource renamed or removed in the central unit drops out
                # of the defaults rather than failing validation.
                vol.Optional(
                    key,
                    default=[n for n in self._options.get(key, []) if n in names],
                ): _pick_many(names)
                for key, names in available.items()
            }
        )
        return self.async_show_form(step_id="sensors", data_schema=schema)

    # ---------------------------------------------------------------- covers

    async def async_step_add_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._cover_form("add_cover", None, user_input)

    async def _async_step_edit_cover(
        self, index: int, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if index >= len(self._options.get(OPT_COVERS, [])):
            return await self.async_step_covers()
        return await self._cover_form(f"cover_{index}", index, user_input)

    async def _cover_form(
        self, step_id: str, index: int | None, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        try:
            sensors = await self._resources(ImportTypes.SENSOR)
        except NexoError as err:
            return self._cannot_list(err)

        covers = self._options.setdefault(OPT_COVERS, [])
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.pop("delete", False) and index is not None:
                del covers[index]
                return await self.async_step_covers()
            if index is None and _is_empty(
                user_input, ITEM_NAME, COVER_OPEN_COMMAND, COVER_CLOSE_COMMAND
            ):
                return await self.async_step_covers()
            user_input[ITEM_NAME] = user_input.get(ITEM_NAME, "").strip()
            if not user_input[ITEM_NAME]:
                errors[ITEM_NAME] = "required"
            for key in (COVER_OPEN_COMMAND, COVER_CLOSE_COMMAND):
                user_input[key] = user_input.get(key, "").strip()
                if error := _check_command(user_input[key]):
                    errors[key] = error
            if user_input[COVER_OPEN_ONLY_WHEN_CLOSED] and not user_input.get(
                COVER_REED_SENSOR
            ):
                errors[COVER_OPEN_ONLY_WHEN_CLOSED] = "guard_needs_reed_sensor"
            if not errors:
                if index is None:
                    covers.append({ITEM_ID: uuid.uuid4().hex, **user_input})
                else:
                    covers[index] = {ITEM_ID: covers[index][ITEM_ID], **user_input}
                return await self.async_step_covers()

        # Optional in the schema so that an empty form can mean "back";
        # required fields are checked above.
        fields: dict[Any, Any] = {
            vol.Optional(ITEM_NAME): TextSelector(),
            vol.Required(COVER_DEVICE_CLASS, default="gate"): SelectSelector(
                SelectSelectorConfig(
                    options=COVER_DEVICE_CLASSES,
                    translation_key="cover_device_class",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(COVER_OPEN_COMMAND): TextSelector(),
            vol.Optional(COVER_CLOSE_COMMAND): TextSelector(),
            vol.Optional(COVER_REED_SENSOR): _pick_one(sensors),
            vol.Required(COVER_OPEN_ONLY_WHEN_CLOSED, default=False): BooleanSelector(),
        }
        if index is not None:
            fields[vol.Optional("delete", default=False)] = BooleanSelector()
        if user_input is not None:
            suggested = user_input
        else:
            suggested = covers[index] if index is not None else None
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            errors=errors,
        )

    # --------------------------------------------------------------- buttons

    async def async_step_add_button(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._button_form("add_button", None, user_input)

    async def _async_step_edit_button(
        self, index: int, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if index >= len(self._options.get(OPT_BUTTONS, [])):
            return await self.async_step_buttons()
        return await self._button_form(f"button_{index}", index, user_input)

    async def _button_form(
        self, step_id: str, index: int | None, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        buttons = self._options.setdefault(OPT_BUTTONS, [])
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.pop("delete", False) and index is not None:
                del buttons[index]
                return await self.async_step_buttons()
            if index is None and _is_empty(user_input, ITEM_NAME, ITEM_COMMAND):
                return await self.async_step_buttons()
            user_input[ITEM_NAME] = user_input.get(ITEM_NAME, "").strip()
            if not user_input[ITEM_NAME]:
                errors[ITEM_NAME] = "required"
            user_input[ITEM_COMMAND] = user_input.get(ITEM_COMMAND, "").strip()
            if error := _check_command(user_input[ITEM_COMMAND]):
                errors[ITEM_COMMAND] = error
            if not errors:
                if index is None:
                    buttons.append({ITEM_ID: uuid.uuid4().hex, **user_input})
                else:
                    buttons[index] = {ITEM_ID: buttons[index][ITEM_ID], **user_input}
                return await self.async_step_buttons()

        fields: dict[Any, Any] = {
            vol.Optional(ITEM_NAME): TextSelector(),
            vol.Optional(ITEM_COMMAND): TextSelector(),
        }
        if index is not None:
            fields[vol.Optional("delete", default=False)] = BooleanSelector()
        if user_input is not None:
            suggested = user_input
        else:
            suggested = buttons[index] if index is not None else None
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            errors=errors,
        )

    # -------------------------------------------------------------- settings

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._options[OPT_SCAN_INTERVAL] = int(user_input[OPT_SCAN_INTERVAL])
            return await self.async_step_menu()

        schema = vol.Schema(
            {
                vol.Required(
                    OPT_SCAN_INTERVAL,
                    default=self._options.get(OPT_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
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


_Step = Callable[
    [NexoOptionsFlow, dict[str, Any] | None], Coroutine[Any, Any, ConfigFlowResult]
]


def _edit_step(kind: str, index: int) -> _Step:
    async def step(
        self: NexoOptionsFlow, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        handler = getattr(self, f"_async_step_edit_{kind}")
        return await handler(index, user_input)

    step.__name__ = f"async_step_{kind}_{index}"
    return step


for _index in range(MAX_ITEMS):
    for _kind in ("cover", "button"):
        setattr(NexoOptionsFlow, f"async_step_{_kind}_{_index}", _edit_step(_kind, _index))
