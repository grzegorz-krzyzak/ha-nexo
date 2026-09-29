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
| Analogue inputs | `sensor` | raw value, or humidity / soil moisture / percentage with a calibration |
| Outputs, read only | `binary_sensor` | on while switched on; for anything a rule mirrors onto an output |
| Lighting outputs (`LIGHT`) | `light` | on / off |
| Dimmers (`DIMMER`) | `light` | with brightness |
| Outputs and lighting outputs, switched | `switch` | on / off, e.g. a ventilation unit |
| Gates and doors driven by logic commands | `cover` | closed / not closed, from a reed switch |
| Watering programs and other start/stop command pairs | `valve` | open while any of its sections is on |
| Any logic command | `button` | fire and forget |

Thermostats, blinds, alarm partitions and the central unit's lighting groups
are not supported yet; for groups, use Home Assistant's own.

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
  costs one query of about 50 ms each time it is read.
- **Analog inputs** - the type and calibration of each imported analog input
  (see *Analog inputs*).
- **Lights and switches** - four screens in a row: excluded resources, lights,
  dimmers, switches (see *Lights and switches*).
- **Gates and doors**, **Valves** and **Buttons** - one entry each; pick one to
  edit or delete it. Up to 20 of each.
- **Settings** - how often each group of resources is read (see *Polling*).

Nothing is stored until **Save and close**; closing the dialog discards the
changes. Home Assistant forms have no back button: submitting a form without
changes - or an empty *add* form - goes back to the menu.

### Lights and switches

Nothing is added on its own: every light, dimmer and switch is picked by hand.

**Exclude first.** Central units often drive a gate's pulses or a door
strike from lighting outputs, for want of free relay outputs, and the
integration cannot tell them from lights by name. A switch in Home Assistant
would fire them in one click - or with a voice command such as "turn
everything off in the garage". So the first screen is a list of resources
never to offer. Put every output that drives a gate, door or lock there once;
from then on it is left out of the lists of lights, switches and valve
outputs. With no such outputs, leave the list empty and go on - the screen
asks for nothing. Outputs already used by a valve or read as sensors are not
offered either.

The state always comes from reading the resource, never from the command: a
command the central unit refuses, or one lost with the connection, shows an
error and leaves the entity as it was. After a command the resource is read
every second for a few seconds, so a change from Home Assistant shows at
once; a change at a wall switch or in another app shows within the *Lights*
interval.

A dimmer's state carries its level (0-255) in the high byte. Switched on
without a brightness, a dimmer gets the central unit's own "on" command, as
from NexoVision - the integration does not change what "on" means there. A
brightness, from a slider or a voice command, is written as a level.

### Analog inputs

An analog input reads 0-100 of the range set for its sensor in the central
unit - a 0-10 V humidity sensor, a photoresistor - and the central unit does
not say what is wired in. Each input's **type** decides how it shows:

| Type | Shown as |
|---|---|
| Raw value (default) | a plain number, e.g. for a resistor-ladder switch |
| Air humidity | %, humidity sensor |
| Soil moisture | %, moisture sensor |
| Percentage | %, e.g. a light level |

A **calibration** of -10 to +10 is added to the reading - the central unit
offers one only for thermometers. A percentage stays within 0-100.

### Polling

Resources are read in groups, each at its own interval:

| Group | What | Default |
|---|---|---|
| Inputs | reed switches and motion sensors, including the gates' reed switches | 5 s |
| Outputs | watering sections, the main valve, outputs read as sensors | 10 s |
| Lights | lights, dimmers and switches | 10 s |
| Measurements | thermometers and analog inputs | 60 s |

After a command, what shows its effect is read every second for a while:
a gate's reed switch for its travel time (60 s without one), a valve's
sections and main valve for 30 s. So an opened gate shows as open within
about a second, whatever the interval.

The central unit answers one read at a time, about 20 a second, however many
connections ask. So each second's reads go in order of urgency: what a
command boosted, then inputs, outputs, lights and measurements. A reed switch
never waits behind a long list of lights.

Updating from 0.2.x: the single polling interval is replaced by these
groups and starts from the defaults.

### Connection status

The device has a diagnostic **Connection to central unit** sensor, on while
the central unit answers - usable in automations, with its history recorded.
After three read rounds in a row without an answer (about 15 s with the
default inputs interval) the integration reloads, so the integrations page
shows it as retrying setup, and it keeps retrying until the central unit is
back. The central unit's firmware version is shown in the device info.

## Gates and doors

The integration does not pulse outputs itself. A gate is driven by **logic
commands**: rules in the central unit whose condition is an external
command, triggered with one atomic `system logic <command>`. The sequence runs
inside the central unit, so a client dying mid-command cannot leave an output
energised.

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

### Step button

A drive with separate up and down inputs stops when it gets the opposite
command while moving - that is how a remote does *up, stop, down, stop*. Home
Assistant's toggle cannot do that: it always closes a gate that is not closed,
and dashboard or car widgets often just alternate open and close.

Set a **travel time** on the gate - its full travel time plus a margin for
communication - and it gets a **Step** button that works like the remote,
and its toggle does the same. The integration remembers the direction and
start of the last movement; after the travel time it assumes the gate has
stopped, and the reed switch reading closed resets the cycle. A remote or
another app used in between is not seen, so one press may fall out of step;
the next closed reading puts it right. Not available together with *Open only
when confirmed closed*, which would refuse the stop on the way down.

A button's state is always the time of its last press, so the button shows
the gate's state in its **icon**, from the reed switch: closed, not closed,
or unreadable (for example `mdi:garage`, `mdi:garage-open`,
`mdi:garage-alert`). Useful where a car widget shows only the button. Nothing
in between is guessed from the travel time. An icon set by hand on the entity
overrides it.

A door strike behind a logic rule is best added as a **button**: the strike
pulse is over before the command returns, and the reed switch does not change
until someone pushes the door, so there is nothing to report but "sent".

## Valves

A **valve** is a pair of logic commands - start and stop - for a watering
program or anything similar. Its state comes from the outputs the program
switches:

- **Sections** - the outputs the program turns on one after another. The
  valve is open while any of them is on. Without sections the state is unknown
  and both commands stay available.
- **Main valve** - an output that stays on for the whole run. Programs usually
  pause for a few seconds between sections; while the main valve is on, the
  program whose section ran last still counts as running. Several valves can
  share one main valve.

Only outputs are observed, not commands, so a program started from the Nexo
app, a remote or the central unit's own schedule shows up the same way.
Closing the main valve ends every run.

**Close automatically after** sends the stop command after the given time -
a convenience for programs the central unit does not end on its own. The timer
lives in Home Assistant and is lost on a restart: if watering must end, give
the rule its own duration in the central unit.

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

The LAN card closes a connection that has been silent longer than the
tolerance for communication breaks set in the central unit's LAN card
settings (5 s by default). With polling intervals longer than that, the
first read after a pause would find the connection gone and have to
reconnect. So once the connection has been quiet for 3 s the integration
sends a `ping`, which the card answers itself without involving the central
unit.

## Credits

The client is a rewrite of `pyNexo.py` from
[Tymec/NexoAPI](https://github.com/Tymec/NexoAPI) (MIT). See `LICENSE`.
