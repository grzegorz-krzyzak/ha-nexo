# Nexwell Nexo for Home Assistant

A Home Assistant integration for the Nexwell Nexo home
automation system, talking to the central unit over its LAN card - the same
connection the NexoVision app uses. Local polling, no cloud.

> **Unofficial.** This project is not affiliated with, endorsed by or supported
> by Nexwell Engineering. Please do not contact Nexwell Engineering support
> about it - open an issue here instead. Nexwell and Nexo are trademarks of
> their respective owner.

## What it provides

| Nexo | Home Assistant | Notes |
|---|---|---|
| Inputs (`SENSOR`) - reed switches, motion detectors | `binary_sensor` | on when violated; pick the type under *Show as* |
| Thermometers | `sensor` | °C |
| Analogue inputs | `sensor` | raw value, no unit |
| Gates and doors driven by logic commands | `cover` | closed / not closed, from a reed switch |
| Any logic command | `button` | fire and forget |

Lights, dimmers, outputs, thermostats, blinds and alarm partitions are not
supported yet.

## Installation

Through [HACS](https://hacs.xyz):

1. HACS → ⋮ → *Custom repositories* → add
   `https://github.com/grzegorz-krzyzak/ha-nexo`, type *Integration*.
2. Install *Nexwell Nexo* and restart Home Assistant.
3. *Settings → Devices & services → Add integration → Nexwell Nexo*.

Manually: copy `custom_components/nexo` into your `config/custom_components`
and restart.

## Configuration

**Connection:** the LAN card's IP address, port `1024` and the NexoVision
password (PIN). Port `1025` serves the remote panel with a different protocol
and will not work. The entry is named after the address, e.g.
*Nexo · 192.168.0.100:1024*, and follows it when the address changes - unless
you rename it.

**Everything else is in the integration's options** (*Configure*), a menu that
shows the current settings next to each item:

- **Connection** - address, port and PIN. The connection status is shown at
  the top of the menu. Also available as *Reconfigure* in the ⋮ menu.
  Entities are kept when the address changes.
- **Sensors** - the lists come from the central unit. Each imported resource
  costs one query of about 50 ms per polling cycle.
- **Gates and doors** and **Buttons** - one entry each; pick one to edit or
  delete it. Up to 20 of each.
- **Settings** - polling interval, 10 s by default.

Nothing is stored until **Save and close**; closing the dialog discards the
changes.

### Connection status

The device has a diagnostic **Connection to central unit** sensor, on while
the central unit answers - usable in automations, with its history recorded.
After three polling cycles in a row without an answer the integration reloads,
so the integrations page shows it as retrying setup, and it keeps retrying
until the central unit is back. The central unit's firmware version is shown
in the device info.

## Gates and doors

The integration does not pulse outputs itself. A gate is driven by **logic
commands**: rules in the central unit whose condition is an external command
(*Komenda zewnętrzna*), triggered with one atomic `system logic <command>`.
The sequence runs inside the central unit, so a client dying mid-command
cannot leave an output energised.

Create the rules in the Nexo configurator first, then add the gate here with
its open command, close command and - optionally - its reed switch. Without
one the gate still works, but its state shows as unknown and nothing can
confirm that it moved.

**Open only when confirmed closed** needs the reed switch: it reads it
immediately before opening and refuses - visibly, as an error - if the gate is
not closed. On a
gate whose drive treats a second command during travel as *stop*, this
prevents an accidental halt. Leave it off where stopping part-way is exactly
what you want, such as a garage door you often leave half-open. Closing is
always unconditional: it is harmless on a closed gate and works even when the
reed switch cannot be read.

Things worth knowing:

- **A mistyped command fails silently.** The central unit acknowledges any
  logic command the same way, whether a rule matches it or not. If nothing
  moves, check the spelling in the configurator. Commands are at most 7
  characters.
- **A reed switch only knows closed and not closed.** There is no opening,
  closing or position state, and none is guessed.
- **The open command's own reply confirms nothing.** Only the reed switch
  changing does - within a few seconds for opening, after the full travel time
  for closing.

A door strike behind a logic rule is best added as a **button**: the strike
pulse is over before the command returns, and the reed switch does not change
until someone pushes the door, so there is nothing to report but "sent".

## Protocol notes

The text commands follow the manufacturer's
[NexoTalk specification](https://github.com/nexwell-mk/nexo-api). State reads
use the numeric `system C` query, which that specification does not cover and
which is reverse-engineered:

| Resource | Numeric state |
|---|---|
| Input | `101` intact, `102` violated |
| Thermometer | tenths of a degree: `233` = 23.3 °C |
| Lighting output | `65281` (`0xFF01`) on, `0` off - but it is *written* as `1` |

Replies are not tagged with the query they answer. Under load a reply can
arrive late and be taken for the next query's answer; the client detects this
by the resource name echoed back and resynchronises, and the integration
treats a failed read as "no news" rather than a change of state. Resource
listings are checked the same way, by the type and index each entry echoes
back.

## Credits

The client is a rewrite of `pyNexo.py` from
[Tymec/NexoAPI](https://github.com/Tymec/NexoAPI) (MIT). See `LICENSE`.
