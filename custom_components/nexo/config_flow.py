"""Config and options flow for the Nexwell Nexo integration."""

from __future__ import annotations

from collections.abc import Callable, Coroutine, Iterable, Mapping
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

from . import weather
from .const import (
    ANALOG_KIND,
    ANALOG_KIND_RAW,
    ANALOG_KINDS,
    ANALOG_OFFSET,
    ANALOG_OFFSET_LIMIT,
    COVER_CLOSE_COMMAND,
    COVER_DEVICE_CLASS,
    COVER_OPEN_COMMAND,
    COVER_OPEN_ONLY_WHEN_CLOSED,
    COVER_REED_SENSOR,
    COVER_TRAVEL_TIME,
    DEFAULT_PORT,
    DEFAULT_INTERVAL_INPUTS,
    DEFAULT_INTERVAL_LIGHTS,
    DEFAULT_INTERVAL_MEASUREMENTS,
    DEFAULT_INTERVAL_OUTPUTS,
    DOMAIN,
    ITEM_COMMAND,
    ITEM_ID,
    ITEM_NAME,
    MAX_ITEMS,
    MAX_LOGIC_COMMAND,
    LEGACY_OPT_SCAN_INTERVAL,
    MAX_INTERVAL,
    MIN_INTERVAL,
    OPT_ANALOG_OUTPUTS,
    OPT_ANALOG_SENSORS,
    OPT_ANALOG_SETTINGS,
    OPT_OUTPUT_SENSORS,
    OPT_PARTITIONS,
    OPT_BINARY_SENSORS,
    OPT_BUTTONS,
    OPT_COVERS,
    OPT_DIMMERS,
    OPT_EXCLUDED,
    OPT_INTERVAL_INPUTS,
    OPT_INTERVAL_LIGHTS,
    OPT_INTERVAL_MEASUREMENTS,
    OPT_INTERVAL_OUTPUTS,
    OPT_LIGHTS,
    OPT_SWITCHES,
    OPT_THERMOMETERS,
    OPT_THERMOSTATS,
    OPT_VALVES,
    OPT_WEATHER,
    PARTITION_DEFAULT_MODE,
    PARTITION_MODE,
    PARTITION_MODES,
    PARTITION_NAME,
    THERMOSTAT_DIRECTION,
    THERMOSTAT_DIRECTIONS,
    THERMOSTAT_HEAT,
    THERMOSTAT_MAX,
    THERMOSTAT_MIN,
    THERMOSTAT_NAME,
    THERMOSTAT_THERMOMETER,
    VALVE_AUTO_CLOSE,
    VALVE_MAIN,
    VALVE_SECTIONS,
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

# The stored PIN is never sent back to the browser. The field shows this mask
# instead, so it does not look empty; submitting the mask - or nothing -
# keeps the stored PIN. The bullets cannot be encoded in ISO-8859-1, which
# the central unit's password must be, so the mask is never a real PIN.
PIN_MASK = "••••••••"

CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): PORT_SELECTOR,
        vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
    }
)

REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR})

COVER_DEVICE_CLASSES = ["gate", "garage", "door"]

# Polling intervals in the settings, with their defaults, in display order
POLLING_INTERVALS = {
    OPT_INTERVAL_INPUTS: DEFAULT_INTERVAL_INPUTS,
    OPT_INTERVAL_OUTPUTS: DEFAULT_INTERVAL_OUTPUTS,
    OPT_INTERVAL_LIGHTS: DEFAULT_INTERVAL_LIGHTS,
    OPT_INTERVAL_MEASUREMENTS: DEFAULT_INTERVAL_MEASUREMENTS,
}

# Translations are read twice, and each reader rejects something:
# - the frontend formats them as ICU messages, where <...> is a tag without
#   attributes, so an ha-alert written into a translation fails with
#   INVALID_TAG - its tags come in as placeholder values instead;
# - the backend validates them with Python's string.Formatter, which rejects
#   ICU select and plural - so a word that depends on the state cannot be
#   picked inside a translation. The menu comes in one variant per
#   connection state instead, each with plain text in the user's language.
NONE = "—"

# Ticked, a list to pick from comes back with everything selected
SELECT_ALL = "select_all"
MENU_STEPS = {"connected": "menu", "not_answering": "menu_offline", "unsaved": "menu_unsaved"}
ALERT_TYPES = {"connected": "success", "not_answering": "warning", "unsaved": "info"}


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
        CONF_PASSWORD: _new_pin(user_input) or entry.data[CONF_PASSWORD],
    }


def _new_pin(user_input: dict[str, Any]) -> str | None:
    pin = user_input.get(CONF_PASSWORD)
    return None if not pin or pin == PIN_MASK else pin


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
    # custom_value is what makes the frontend render a single choice with a
    # search box; anything typed is checked against the list on submit.
    return SelectSelector(
        SelectSelectorConfig(
            options=names, sort=True, custom_value=True, mode=SelectSelectorMode.DROPDOWN
        )
    )


def _sections_summary(sections: list[str]) -> str:
    """'NAWODNIENIE S1 … S6' rather than six full names, without words that
    would need translating."""
    if len(sections) <= 1:
        return "".join(sections)
    prefix = ""
    first = sections[0]
    if " " in first:
        candidate = first.rsplit(" ", 1)[0] + " "
        if all(s.startswith(candidate) for s in sections):
            prefix = candidate
    rest = [s[len(prefix):] for s in sections]
    joined = f"{rest[0]} … {rest[-1]}" if len(rest) > 2 else ", ".join(rest)
    return prefix + joined


def _analog_placeholders(index: int, item: dict[str, Any]) -> dict[str, str]:
    """Language-free values for an analog input's menu line.

    The words come from the translations, which the frontend picks in the
    viewer's language; a flow does not know that language, so nothing here
    may be text.
    """
    kind = item.get(ANALOG_KIND, ANALOG_KIND_RAW)
    return {
        f"analog_{index}_unit": "—" if kind == ANALOG_KIND_RAW else "%",
        f"analog_{index}_offset": f"{int(item.get(ANALOG_OFFSET, 0)):+d}",
    }


def _cover_summary(item: dict[str, Any]) -> str:
    parts = [f"{item[COVER_OPEN_COMMAND]} / {item[COVER_CLOSE_COMMAND]}"]
    if item.get(COVER_REED_SENSOR):
        parts.append(item[COVER_REED_SENSOR])
    if item.get(COVER_TRAVEL_TIME):
        parts.append(f"{item[COVER_TRAVEL_TIME]:g} s")
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
            CONF_PASSWORD: PIN_MASK,
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

    Each gate, valve and button gets its own menu entry, which needs a step
    per entry: cover_0 ... cover_19, valve_0 ... valve_19 and button_0 ...
    button_19 are generated below the class.
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
        # own icon and colour for each type - only when something needs
        # attention. Connected, the status sits next to the address instead,
        # so the menu fits on one screen.
        if self._connection is not None:
            status = "unsaved"
        elif self.config_entry.runtime_data.coordinator.last_update_success:
            status = "connected"
        else:
            status = "not_answering"
        return self.async_show_menu(
            step_id=MENU_STEPS[status],
            # By the central unit's resource types, named as in the installer's
            # manual (decision 2026-10-04): a user finds a resource where Nexo
            # files it, not where this house happens to use it. Between
            # connection and settings in the order of the Polish names, as
            # NexoVision words them; one order serves every language
            menu_options=[
                "connection", "sensors", "logic", "lights", "partitions", "weather", "dimmers",
                "thermometers", "thermostats", "analog", "outputs", "analog_outputs",
                "settings", "save",
            ],
            description_placeholders={
                "address": f"{connection[CONF_HOST]}:{connection.get(CONF_PORT, DEFAULT_PORT)}",
                "alert_open": f'<ha-alert alert-type="{ALERT_TYPES[status]}">',
                "alert_close": "</ha-alert>",
                OPT_BINARY_SENSORS: str(len(options.get(OPT_BINARY_SENSORS, []))),
                OPT_THERMOMETERS: str(len(options.get(OPT_THERMOMETERS, []))),
                OPT_ANALOG_SENSORS: str(len(options.get(OPT_ANALOG_SENSORS, []))),
                OPT_OUTPUT_SENSORS: str(len(options.get(OPT_OUTPUT_SENSORS, []))),
                OPT_THERMOSTATS: str(len(options.get(OPT_THERMOSTATS, []))),
                "logic_items": str(
                    sum(len(options.get(k, [])) for k in (OPT_COVERS, OPT_BUTTONS, OPT_VALVES))
                ),
                OPT_ANALOG_OUTPUTS: str(len(options.get(OPT_ANALOG_OUTPUTS, []))),
                OPT_PARTITIONS: str(len(options.get(OPT_PARTITIONS, []))),
                **{
                    key: str(len(options.get(key, [])))
                    for key in (OPT_LIGHTS, OPT_DIMMERS, OPT_SWITCHES, OPT_EXCLUDED)
                },
                OPT_COVERS: ", ".join(i[ITEM_NAME] for i in options.get(OPT_COVERS, []))
                or NONE,
                OPT_BUTTONS: ", ".join(i[ITEM_NAME] for i in options.get(OPT_BUTTONS, []))
                or NONE,
                OPT_VALVES: ", ".join(i[ITEM_NAME] for i in options.get(OPT_VALVES, []))
                or NONE,
                **{
                    key: str(options.get(key, default))
                    for key, default in POLLING_INTERVALS.items()
                },
            },
        )

    # The variants of the main menu - Home Assistant needs a step for every
    # step id it shows.
    async_step_menu_offline = async_step_menu
    async_step_menu_unsaved = async_step_menu

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
        menu.append("logic")
        placeholders: dict[str, str] = {}
        for i, item in enumerate(covers):
            placeholders[f"cover_{i}"] = item[ITEM_NAME]
            placeholders[f"cover_{i}_info"] = _cover_summary(item)
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
        menu.append("logic")
        placeholders: dict[str, str] = {}
        for i, item in enumerate(buttons):
            placeholders[f"button_{i}"] = item[ITEM_NAME]
            placeholders[f"button_{i}_info"] = item[ITEM_COMMAND]
        return self.async_show_menu(
            step_id="buttons", menu_options=menu, description_placeholders=placeholders
        )

    async def async_step_valves(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        valves = self._options.get(OPT_VALVES, [])
        menu = [f"valve_{i}" for i in range(len(valves))]
        if len(valves) < MAX_ITEMS:
            menu.append("add_valve")
        menu.append("logic")
        placeholders: dict[str, str] = {}
        for i, item in enumerate(valves):
            placeholders[f"valve_{i}"] = item[ITEM_NAME]
            info = f"{item[COVER_OPEN_COMMAND]} / {item[COVER_CLOSE_COMMAND]}"
            if item.get(VALVE_SECTIONS):
                info += f" · {_sections_summary(item[VALVE_SECTIONS])}"
            placeholders[f"valve_{i}_info"] = info
        return self.async_show_menu(
            step_id="valves", menu_options=menu, description_placeholders=placeholders
        )

    async def async_step_analog(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        names = self._options.get(OPT_ANALOG_SENSORS, [])[:MAX_ITEMS]
        settings = self._options.get(OPT_ANALOG_SETTINGS, {})
        placeholders: dict[str, str] = {}
        for i, name in enumerate(names):
            placeholders[f"analog_{i}"] = name
            placeholders.update(_analog_placeholders(i, settings.get(name, {})))
        return self.async_show_menu(
            step_id="analog",
            menu_options=["analog_pick", *(f"analog_{i}" for i in range(len(names))), "back"],
            description_placeholders=placeholders,
        )

    async def async_step_analog_pick(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            names = await self._resources(ImportTypes.ANALOGSENSOR)
        except NexoError as err:
            return self._cannot_list(err)
        if user_input is not None:
            self._options[OPT_ANALOG_SENSORS] = user_input.get(OPT_ANALOG_SENSORS, [])
            selected = set(self._options[OPT_ANALOG_SENSORS])
            settings = self._options.get(OPT_ANALOG_SETTINGS, {})
            for name in [n for n in settings if n not in selected]:
                del settings[name]
            return await self.async_step_analog()
        return self._pick_form("analog_pick", OPT_ANALOG_SENSORS, names)

    async def _async_step_edit_analog(
        self, index: int, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        names = self._options.get(OPT_ANALOG_SENSORS, [])
        if index >= len(names):
            return await self.async_step_analog()
        name = names[index]
        settings: dict[str, Any] = self._options.setdefault(OPT_ANALOG_SETTINGS, {})
        if user_input is not None:
            item = {
                ANALOG_KIND: user_input[ANALOG_KIND],
                ANALOG_OFFSET: int(user_input.get(ANALOG_OFFSET) or 0),
            }
            if item == {ANALOG_KIND: ANALOG_KIND_RAW, ANALOG_OFFSET: 0}:
                settings.pop(name, None)  # the default needs no entry
            else:
                settings[name] = item
            return await self.async_step_analog()

        current = settings.get(name, {})
        schema = vol.Schema(
            {
                vol.Required(
                    ANALOG_KIND, default=current.get(ANALOG_KIND, ANALOG_KIND_RAW)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=ANALOG_KINDS,
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key="analog_kind",
                    )
                ),
                vol.Required(
                    ANALOG_OFFSET, default=current.get(ANALOG_OFFSET, 0)
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=-ANALOG_OFFSET_LIMIT,
                        max=ANALOG_OFFSET_LIMIT,
                        step=1,
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id=f"analog_{index}",
            data_schema=schema,
            description_placeholders={"resource": name},
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
            CONF_PASSWORD: PIN_MASK,
        }
        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(CONNECTION_SCHEMA, suggested),
            errors=errors,
        )

    # ------------------------------------------- lights, dimmers and outputs
    #
    # Lighting outputs and outputs each have one screen with three fields:
    # what Home Assistant controls, and the ones never to offer - outputs
    # that drive gates and locks, which a switch would fire in one click. A
    # resource in two fields is a form error; setup also keeps the safest
    # role of options that arrive otherwise (roles.py). The stored keys are
    # the same as before 0.11 - lights, switches, output_sensors, excluded -
    # each screen edits only its own type's part of them.

    def _pickable(self, names: list[str], keep: Iterable[str] = ()) -> list[str]:
        """The names minus the excluded ones, except those in keep."""
        excluded = set(self._options.get(OPT_EXCLUDED, [])) - set(keep)
        return [name for name in names if name not in excluded]

    def _in_use(self) -> set[str]:
        """Outputs a valve already reads or drives."""
        valves = self._options.get(OPT_VALVES, [])
        return {
            *(section for valve in valves for section in valve.get(VALVE_SECTIONS, [])),
            *(valve[VALVE_MAIN] for valve in valves if valve.get(VALVE_MAIN)),
        }

    def _pick_form(
        self,
        step_id: str,
        key: str,
        names: list[str],
        offer_all: bool = False,
        selected: list[str] | None = None,
    ) -> ConfigFlowResult:
        if selected is None:
            selected = [n for n in self._options.get(key, []) if n in names]
        fields: dict[Any, Any] = {vol.Optional(key, default=selected): _pick_many(names)}
        if offer_all:
            fields[vol.Optional(SELECT_ALL, default=False)] = BooleanSelector()
        return self.async_show_form(step_id=step_id, data_schema=vol.Schema(fields))

    async def _pick_step(
        self,
        step_id: str,
        key: str,
        names: list[str],
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """A list to pick from, with "select all" for long lists: ticking it
        shows the same form again with everything selected, to untick a few -
        nothing is saved until the list itself is submitted."""
        if user_input is not None:
            if user_input.get(SELECT_ALL):
                return self._pick_form(step_id, key, names, offer_all=True, selected=names)
            self._options[key] = user_input.get(key, [])
            return await self.async_step_menu()
        return self._pick_form(step_id, key, names, offer_all=True)

    def _roles_form(
        self,
        step_id: str,
        names: list[str],
        values: dict[str, list[str]],
        errors: dict[str, str] | None = None,
        placeholders: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """The fields top down, each listing what the fields above do not
        hold - fixed while the form is open, so a change shows in the lists
        below once the form comes back."""
        fields: dict[Any, Any] = {}
        above: set[str] = set()
        for key, selected in values.items():
            choices = [n for n in names if n not in above]
            fields[vol.Optional(key, default=[n for n in selected if n in choices])] = (
                _pick_many(choices)
            )
            above |= set(selected)
        fields[vol.Optional(SELECT_ALL, default=False)] = BooleanSelector()
        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(fields),
            errors=errors or {},
            description_placeholders=placeholders or {"resource": ""},
        )

    async def _roles_step(
        self,
        step_id: str,
        names: list[str],
        keys: list[str],
        user_input: dict[str, Any] | None,
    ) -> ConfigFlowResult:
        """Three fields over one list of resources, top down: never offered,
        then the safer role, then the last. Each resource in one field at
        most; "select all" fills the last field with what the others do not
        hold, as they are when it is ticked."""
        mine = set(names)
        if user_input is None:
            values: dict[str, list[str]] = {}
            later: set[str] = set()
            for key in reversed(keys):
                # A resource stored in two fields shows in the lower one - the
                # role it is used in (an output read as a sensor and also never
                # offered, as before 0.11.1, stays read only)
                values[key] = [
                    n for n in self._options.get(key, []) if n in mine and n not in later
                ]
                later |= set(values[key])
            return self._roles_form(step_id, names, {k: values[k] for k in keys})
        values = {k: list(user_input.get(k, [])) for k in keys}
        if user_input.get(SELECT_ALL):
            others = {n for k in keys[:-1] for n in values[k]}
            values[keys[-1]] = [n for n in names if n not in others]
            return self._roles_form(step_id, names, values)
        seen: set[str] = set()
        for key in keys:
            if clash := next((n for n in values[key] if n in seen), None):
                return self._roles_form(
                    step_id, names, values,
                    errors={"base": "role_conflict"}, placeholders={"resource": clash},
                )
            seen |= set(values[key])
        for key in keys:
            # Keep the other types' part of the key, in its order
            old = self._options.get(key, [])
            chosen = set(values[key])
            self._options[key] = [
                *(n for n in old if n not in mine or n in chosen),
                *(n for n in values[key] if n not in old),
            ]
        return await self.async_step_menu()

    async def async_step_lights(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Lighting outputs: lights, switches (a fan wired as a light), never offered."""
        try:
            names = await self._resources(ImportTypes.LIGHT)
        except NexoError as err:
            return self._cannot_list(err)
        in_use = self._in_use()
        names = [n for n in names if n not in in_use]
        return await self._roles_step(
            "lights", names, [OPT_EXCLUDED, OPT_SWITCHES, OPT_LIGHTS], user_input
        )

    async def async_step_outputs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Outputs: switches, read only, never offered."""
        try:
            names = await self._resources(ImportTypes.OUTPUT)
        except NexoError as err:
            return self._cannot_list(err)
        in_use = self._in_use()
        names = [n for n in names if n not in in_use]
        return await self._roles_step(
            "outputs", names, [OPT_EXCLUDED, OPT_OUTPUT_SENSORS, OPT_SWITCHES], user_input
        )

    async def async_step_dimmers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            names = self._pickable(await self._resources(ImportTypes.DIMMER))
        except NexoError as err:
            return self._cannot_list(err)
        return await self._pick_step("dimmers", OPT_DIMMERS, names, user_input)

    async def async_step_thermometers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            names = await self._resources(ImportTypes.THERMOMETER)
        except NexoError as err:
            return self._cannot_list(err)
        return await self._pick_step("thermometers", OPT_THERMOMETERS, names, user_input)

    # ------------------------------------------------------------- partitions

    async def async_step_partitions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The alarm partitions to import - regular and 24h alike; the type
        comes from the central unit's lists. Nothing by default."""
        try:
            regular = await self._resources(ImportTypes.PARTITION)
            always_on = await self._resources(ImportTypes.PARTITION24H)
        except NexoError as err:
            return self._cannot_list(err)
        names = [*regular, *always_on]
        if user_input is not None:
            modes = {
                item[PARTITION_NAME]: item.get(PARTITION_MODE, PARTITION_DEFAULT_MODE)
                for item in self._options.get(OPT_PARTITIONS, [])
            }
            self._options[OPT_PARTITIONS] = [
                {PARTITION_NAME: name, PARTITION_MODE: modes.get(name, PARTITION_DEFAULT_MODE)}
                for name in user_input.get(OPT_PARTITIONS, [])
                if name in names
            ]
            if any(item[PARTITION_NAME] in regular for item in self._options[OPT_PARTITIONS]):
                return await self.async_step_partitions_mode()
            return await self.async_step_menu()
        selected = [
            item[PARTITION_NAME]
            for item in self._options.get(OPT_PARTITIONS, [])
            if item[PARTITION_NAME] in names
        ]
        return self.async_show_form(
            step_id="partitions",
            data_schema=vol.Schema(
                {vol.Optional(OPT_PARTITIONS, default=selected): _pick_many(names)}
            ),
        )

    async def async_step_partitions_mode(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """How Home Assistant shows "armed" for each regular partition - Nexo
        arms a partition one way; this is the label and the one button."""
        try:
            regular = set(await self._resources(ImportTypes.PARTITION))
        except NexoError as err:
            return self._cannot_list(err)
        items = [
            i for i in self._options.get(OPT_PARTITIONS, []) if i[PARTITION_NAME] in regular
        ]
        if user_input is not None:
            for item in items:
                item[PARTITION_MODE] = user_input.get(
                    item[PARTITION_NAME], PARTITION_DEFAULT_MODE
                )
            return await self.async_step_menu()
        mode = SelectSelector(
            SelectSelectorConfig(
                options=PARTITION_MODES,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="partition_mode",
            )
        )
        return self.async_show_form(
            step_id="partitions_mode",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        item[PARTITION_NAME],
                        default=item.get(PARTITION_MODE, PARTITION_DEFAULT_MODE),
                    ): mode
                    for item in items
                }
            ),
        )

    # ------------------------------------------------------------------ logic

    async def async_step_logic(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entities driven by logic commands: gates and doors, buttons, programs."""
        options = self._options
        return self.async_show_menu(
            step_id="logic",
            menu_options=["covers", "buttons", "valves", "back"],
            description_placeholders={
                key: ", ".join(i[ITEM_NAME] for i in options.get(key, [])) or NONE
                for key in (OPT_COVERS, OPT_BUTTONS, OPT_VALVES)
            },
        )

    # ----------------------------------------------------------- thermostats

    async def async_step_thermostats(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The thermostats to import. Each keeps what its list entry carries -
        its thermometer and range - so nothing is typed in. Nothing by default."""
        try:
            hub = self.config_entry.runtime_data.hub
            available = {t.name: t for t in await hub.async_thermostats()}
        except NexoError as err:
            return self._cannot_list(err)
        if user_input is not None:
            directions = {
                item[THERMOSTAT_NAME]: item.get(THERMOSTAT_DIRECTION, THERMOSTAT_HEAT)
                for item in self._options.get(OPT_THERMOSTATS, [])
            }
            self._options[OPT_THERMOSTATS] = [
                {
                    THERMOSTAT_NAME: name,
                    THERMOSTAT_THERMOMETER: available[name].thermometer,
                    THERMOSTAT_MIN: available[name].minimum,
                    THERMOSTAT_MAX: available[name].maximum,
                    THERMOSTAT_DIRECTION: directions.get(name, THERMOSTAT_HEAT),
                }
                for name in user_input.get(OPT_THERMOSTATS, [])
                if name in available
            ]
            if not self._options[OPT_THERMOSTATS]:
                return await self.async_step_menu()
            return await self.async_step_thermostats_direction()
        selected = [
            item[THERMOSTAT_NAME]
            for item in self._options.get(OPT_THERMOSTATS, [])
            if item[THERMOSTAT_NAME] in available
        ]
        return self.async_show_form(
            step_id="thermostats",
            data_schema=vol.Schema(
                {vol.Optional(OPT_THERMOSTATS, default=selected): _pick_many(sorted(available))}
            ),
        )

    async def async_step_thermostats_direction(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Heating or cooling for each picked thermostat - the central unit
        does not reveal it (the hysteresis sign). Heating by default, as in
        the central unit."""
        items = self._options.get(OPT_THERMOSTATS, [])
        if user_input is not None:
            for item in items:
                item[THERMOSTAT_DIRECTION] = user_input.get(item[THERMOSTAT_NAME], THERMOSTAT_HEAT)
            return await self.async_step_menu()
        direction = SelectSelector(
            SelectSelectorConfig(
                options=THERMOSTAT_DIRECTIONS,
                mode=SelectSelectorMode.DROPDOWN,
                translation_key="thermostat_direction",
            )
        )
        return self.async_show_form(
            step_id="thermostats_direction",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        item[THERMOSTAT_NAME],
                        default=item.get(THERMOSTAT_DIRECTION, THERMOSTAT_HEAT),
                    ): direction
                    for item in items
                }
            ),
        )

    # -------------------------------------------------------- analogue outputs

    async def async_step_analog_outputs(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The 0-10 V outputs to import, each as a level 0-100 %. Nothing by
        default."""
        try:
            names = await self._resources(ImportTypes.ANALOG_OUTPUT)
        except NexoError as err:
            return self._cannot_list(err)
        if user_input is not None:
            self._options[OPT_ANALOG_OUTPUTS] = [
                name for name in user_input.get(OPT_ANALOG_OUTPUTS, []) if name in names
            ]
            return await self.async_step_menu()
        selected = [n for n in self._options.get(OPT_ANALOG_OUTPUTS, []) if n in names]
        return self.async_show_form(
            step_id="analog_outputs",
            data_schema=vol.Schema(
                {vol.Optional(OPT_ANALOG_OUTPUTS, default=selected): _pick_many(sorted(names))}
            ),
        )

    # -------------------------------------------------------- weather station

    async def async_step_weather(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Import the weather station card's readings, or not. One switch:
        the card always has the same five resources. Off by default."""
        try:
            names = await self._resources(ImportTypes.WEATHER_STATION)
        except NexoError as err:
            return self._cannot_list(err)
        present = len(names) == weather.RESOURCES
        if user_input is not None:
            if present and user_input.get(OPT_WEATHER):
                self._options[OPT_WEATHER] = names
            else:
                self._options.pop(OPT_WEATHER, None)
            return await self.async_step_menu()
        if not present:
            # Nothing to import: the form only says so, and goes back
            return self.async_show_form(
                step_id="weather", data_schema=vol.Schema({}),
                errors={"base": "no_weather_station"},
            )
        return self.async_show_form(
            step_id="weather",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_WEATHER, default=bool(self._options.get(OPT_WEATHER))
                    ): BooleanSelector()
                }
            ),
        )

    # --------------------------------------------------------------- sensors

    async def async_step_sensors(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Inputs - reed switches, motion detectors."""
        try:
            names = await self._resources(ImportTypes.SENSOR)
        except NexoError as err:
            return self._cannot_list(err)
        if user_input is not None:
            self._options[OPT_BINARY_SENSORS] = user_input.get(OPT_BINARY_SENSORS, [])
            return await self.async_step_menu()
        # A resource renamed or removed in the central unit drops out of the
        # defaults rather than failing validation
        return self._pick_form("sensors", OPT_BINARY_SENSORS, names)

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
            reed = user_input.get(COVER_REED_SENSOR)
            if reed and reed not in sensors:
                errors[COVER_REED_SENSOR] = "unknown_resource"
            if user_input[COVER_OPEN_ONLY_WHEN_CLOSED] and not user_input.get(
                COVER_REED_SENSOR
            ):
                errors[COVER_OPEN_ONLY_WHEN_CLOSED] = "guard_needs_reed_sensor"
            if not user_input.get(COVER_TRAVEL_TIME):
                user_input.pop(COVER_TRAVEL_TIME, None)
            elif user_input[COVER_OPEN_ONLY_WHEN_CLOSED]:
                # Stepping stops a moving gate with the opposite command,
                # which the guard would refuse on the way down.
                errors[COVER_TRAVEL_TIME] = "step_needs_unguarded"
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
            vol.Optional(COVER_TRAVEL_TIME): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=600, step=1, unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
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

    # ---------------------------------------------------------------- valves

    async def async_step_add_valve(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._valve_form("add_valve", None, user_input)

    async def _async_step_edit_valve(
        self, index: int, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if index >= len(self._options.get(OPT_VALVES, [])):
            return await self.async_step_valves()
        return await self._valve_form(f"valve_{index}", index, user_input)

    async def _valve_form(
        self, step_id: str, index: int | None, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        try:
            outputs = sorted(
                {
                    *await self._resources(ImportTypes.OUTPUT),
                    *await self._resources(ImportTypes.LIGHT),
                }
            )
        except NexoError as err:
            return self._cannot_list(err)

        valves = self._options.setdefault(OPT_VALVES, [])
        # Excluded outputs are not offered, except the ones this valve uses
        current = valves[index] if index is not None else {}
        used = [*current.get(VALVE_SECTIONS, []), *filter(None, [current.get(VALVE_MAIN)])]
        outputs = self._pickable(outputs, keep=used)
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.pop("delete", False) and index is not None:
                del valves[index]
                return await self.async_step_valves()
            if index is None and _is_empty(
                user_input, ITEM_NAME, COVER_OPEN_COMMAND, COVER_CLOSE_COMMAND
            ):
                return await self.async_step_valves()
            user_input[ITEM_NAME] = user_input.get(ITEM_NAME, "").strip()
            if not user_input[ITEM_NAME]:
                errors[ITEM_NAME] = "required"
            for key in (COVER_OPEN_COMMAND, COVER_CLOSE_COMMAND):
                user_input[key] = user_input.get(key, "").strip()
                if error := _check_command(user_input[key]):
                    errors[key] = error
            if user_input.get(VALVE_MAIN) and user_input[VALVE_MAIN] not in outputs:
                errors[VALVE_MAIN] = "unknown_resource"
            elif user_input.get(VALVE_MAIN) in user_input.get(VALVE_SECTIONS, []):
                errors[VALVE_MAIN] = "main_valve_is_a_section"
            for key in (VALVE_SECTIONS, VALVE_MAIN, VALVE_AUTO_CLOSE):
                if not user_input.get(key):
                    user_input.pop(key, None)
            if not errors:
                if index is None:
                    valves.append({ITEM_ID: uuid.uuid4().hex, **user_input})
                else:
                    valves[index] = {ITEM_ID: valves[index][ITEM_ID], **user_input}
                return await self.async_step_valves()

        # Optional in the schema so that an empty form can mean "back";
        # required fields are checked above.
        fields: dict[Any, Any] = {
            vol.Optional(ITEM_NAME): TextSelector(),
            vol.Optional(COVER_OPEN_COMMAND): TextSelector(),
            vol.Optional(COVER_CLOSE_COMMAND): TextSelector(),
            vol.Optional(VALVE_SECTIONS): _pick_many(outputs),
            vol.Optional(VALVE_MAIN): _pick_one(outputs),
            vol.Optional(VALVE_AUTO_CLOSE): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=720, step=1, unit_of_measurement="min",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
        if index is not None:
            fields[vol.Optional("delete", default=False)] = BooleanSelector()
        if user_input is not None:
            suggested = user_input
        else:
            suggested = valves[index] if index is not None else None
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
            for key in POLLING_INTERVALS:
                self._options[key] = int(user_input[key])
            self._options.pop(LEGACY_OPT_SCAN_INTERVAL, None)
            return await self.async_step_menu()

        schema = vol.Schema(
            {
                vol.Required(key, default=self._options.get(key, default)): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_INTERVAL,
                        max=MAX_INTERVAL,
                        step=1,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                )
                for key, default in POLLING_INTERVALS.items()
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
    for _kind in ("cover", "valve", "button", "analog"):
        setattr(NexoOptionsFlow, f"async_step_{_kind}_{_index}", _edit_step(_kind, _index))
