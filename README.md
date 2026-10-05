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
| Thermostats | `climate` | heat or cool (set per thermostat) / off, threshold in tenths - see *Thermostats* |
| Analog outputs | `number` | level 0-100 % - see *Analog outputs* |
| Alarm partitions | `alarm_control_panel` | disarmed / armed / alarming; a code each time; 24h read only - see *Alarm partitions* |
| Weather station card | `sensor`, `binary_sensor` | temperature, daylight, wind, sun from three sides; frost, heat, twilight, sunny, calm, strong wind, rain - see *Weather station* |
| Gates and doors driven by logic commands | `cover` | closed / not closed, from a reed switch |
| Watering programs and other start/stop command pairs | `valve` | open while any of its sections is on |
| Any logic command | `button` | fire and forget |

Not supported:
- **Blinds** - nothing to test them on.
- **The central unit's lighting groups** - use Home Assistant's own; a
  group's state in the central unit depends on its history.
- **"Ventilation (0-10V)" resources** - overlays in the configurator that
  name an analog output; the state lives in the output, import that.

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

**Everything else is in the integration's options** (*Configure*), a menu by
the central unit's resource types - the names of the installer's manual, in
the alphabetical order of the Polish names between *Connection* and
*Settings* - with the current settings next to each item:

- **Connection** - address, port and PIN. The connection status is shown next
  to the address, and at the top of the menu when the central unit is not
  answering or the connection has unsaved changes. Also available as
  *Reconfigure* in the ⋮ menu. Entities are kept when the address changes.
- **Sensors** - inputs: reed switches, motion detectors.
- **Logic** - entities driven by logic commands: **Gates and doors**,
  **Buttons** and **Programs** (started and stopped by a pair of commands,
  such as watering, with relay outputs as sections). Pick one to edit or
  delete it. Up to 20 of each.
- **Lighting** - lighting outputs, each as a light, a switch or not offered
  (see *Lighting and outputs*).
- **Partitions** - the alarm partitions to import, then how "armed" shows
  for each regular one (see *Alarm partitions*).
- **Weather station** - see *Weather station*.
- **Dimmers** - with brightness.
- **Thermometers** - a thermostat's own thermometer is read with it anyway.
- **Thermostats** - the thermostats to import, then heating or cooling for
  each; each brings the thermometer and the range set for it in the central
  unit (see *Thermostats*).
- **Analog inputs** (NexoVision: *Analog sensor*) - the inputs to import, then
  the type and calibration of each (see *Analog inputs*).
- **Outputs** - each as a switch, read only or not offered.
- **Analog outputs** - see *Analog outputs*.
- **Settings** - how often each group of resources is read (see *Polling*).

Each listed resource costs one query of about 50 ms each time it is read.
Nothing is stored until **Save and close**; closing the dialog discards the
changes. Home Assistant forms have no back button: submitting a form without
changes - or an empty *add* form - goes back to the menu.

### Lighting and outputs

Nothing is added on its own: every light, dimmer and switch is picked by hand.

*Lighting* and *Outputs* each have one screen with three fields, top down:
**don't offer**, then switches and lights (*Lighting*) or read only and
switches (*Outputs*). Each list leaves out what the fields above hold, so the
lowest is the shortest; a change shows in the lists below once the form comes
back. A resource goes in one field at most - picked twice, it is refused with
its name. A lighting output can be a switch too, for a fan wired as a light,
say. *Select all* fills the lowest field with everything the others do not
hold, as they are when it is ticked.

**Don't offer the outputs Home Assistant must not control** - a gate's,
door's or lock's pulses, or outputs driven by thermostats. Central units
often drive a gate's pulses or a door strike from lighting outputs, for want
of free relay outputs, and the integration cannot tell them from lights by
name. A switch in Home Assistant would fire them in one click - or with a
voice command such as "turn everything off in the garage". Put them under
*Don't offer* once; they are then left out of the lists below it on the
screen and of the sections list under *Logic → Programs*. Outputs a program
already uses as sections are not listed on these screens.

Options saved before 0.11.1 could hold an output both read only and never
offered; the *Outputs* screen shows it read only and saving settles it there.
If the stored options
still give a resource a controlled role next to another one - edited by hand,
restored from an old backup - setup keeps the safer role (not offered or
read only, then light, dimmer, switch), logs a warning and raises a repair
issue (*Settings → Repairs*) until the options are saved again.

**A resource name shared by two types** - an analog output and a
"Ventilation (0-10V)" overlay named alike, say - makes a read by name get the
wrong one (a level of 0 while the output runs). After setup the integration
lists every type in the background and raises a repair issue for the
resources it uses whose name another type shares; rename one of them in the
configurator.

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

### Thermostats

**Heating or cooling is set by the sign of a thermostat's hysteresis in the
central unit** (the NXW299.2 temperature module manual): positive - the
default - is heating control, negative is cooling. With heating the output
goes on once the room is warmer than the threshold ("warm enough"), with
cooling once it is colder. No query reveals the sign, so after picking the
thermostats the options ask for each one: *Heating* (the default) or
*Cooling*. A thermostat then offers the *heat* or *cool* mode and *off*.

- "Heating" (or "cooling") means the thermostat asks for heat (cold) - its
  output is off; whether heat actually flows is up to its source. "Idle"
  means the room has reached the threshold and the output is on.
- What the output does physically depends on the wiring - a relay offers
  NO / COM / NC; with normally open valves on underfloor loops, for
  instance, the output on closes the room's loop.
- The target temperature is the threshold, in tenths of a degree within the
  range set in the central unit - written the way NexoVision writes it; the
  current temperature comes from the thermometer the thermostat is set to read.
- *Off* switches the thermostat off in the central unit, keeping its
  threshold; its output goes off.
- Setting the threshold keeps the mode: the threshold and whether the
  thermostat is on go in one command, so a thermostat that is off stays off.
  An active thermostat applies a new threshold at once; its relay follows in a
  few seconds.
- After a change in the configurator - the hysteresis, say - the central unit
  keeps the output as it was until the next threshold change; set the
  threshold again to apply it.
- Thermostats are read with the *Measurements* group (60 s by default) and
  every second for a few seconds after a command.

### Alarm partitions

*Partitions* imports the central unit's alarm partitions as alarm panels.

- **States, as the central unit reports them:** disarmed, armed, alarming.
  It reports no exit or entry delay, so there is no "arming" or "pending".
  *Alarming* is the partition's alarm scheme running - for a scheme that
  only sends a message and ends, a few seconds; with a wait in it, as long as
  the wait. Partitions are read with the *Inputs* group (5 s by default).
- **A code each time.** Arming and disarming ask for the user's alarm code,
  as on the wall panel; it is sent to the central unit once and never
  stored, retried or logged. Three wrong codes start the alarm scheme in the
  central unit, so a lost confirmation is reported - check the state - and
  not sent again.
- **A wrong code** is refused by the central unit in its own words; the
  panel says so, and the integration fires `nexo_wrong_code` (`partition`,
  `action`) for automations - a notification, say. Only codes entered in
  Home Assistant are seen; a wrong one at the wall panel stays in the
  central unit.
- **Arming is refused with a sensor violated** - a window open - or faulty;
  the central unit's reply is shown.
- **One button.** Nexo arms a partition one way; for each regular partition
  the options pick how "armed" shows - away (the default), home, night or
  vacation - and the panel offers that one button.
- **24h partitions** (fire, flood) are listed by the central unit under
  their own type: shown read only, without buttons - they are not disarmed.
  An alarm there is stopped with "Wyczyść alarm" on the wall panel or in
  NexoVision.

### Analog outputs

The options' *Analog outputs* imports the outputs of the analog output
module - a ventilation unit's speed, the level of a dimmed LED supply - each
as a slider from 0 to 100 %.

- The entity is **the level of the signal, not the state of the device**.
  The level alone switches nothing on: a device usually has its own output
  for power, and may keep working at 0 % - a dimmed light glows, a
  ventilation unit runs at its lowest speed. Compose the two
  in Home Assistant - a template light, say - if you want one entity; the
  integration does not guess how a house is wired.
- The percentage is the central unit's own (it rounds down: a level of 191
  reads "74%"); a value set here reads back the same.
- What 0 % and 100 % mean in volts depends on the output's settings in the
  configurator: range 0-10 V, 1-10 V or 0-12 V PWM, positive or negative
  logic, processing function. They are not mapped - the integration works
  with the level only.
- Read with the *Lights* group (10 s by default): a level changes on command,
  from the logic or from a wall button.

### Weather station

With Nexwell's weather station card (an Elsner P03/3-RS485 station), the
options offer *Weather station*: one switch imports all its readings, on a
device of its own linked to the central unit. Without the card the screen
says so.

| Entity | Shown as |
|---|---|
| Temperature | °C, one decimal place |
| Daylight | 0-999 lx - it saturates long before full daylight |
| Wind speed | km/h by default; any unit Home Assistant offers - m/s, knots, Beaufort - in the entity's settings |
| Sun west, south, east | klx, 0-99 each |
| Frost, heat, twilight, sunny, calm, strong wind, rain | on / off, as the station judges them |
| Conditions code | the raw bits behind the on / off conditions, diagnostic |

- The conditions use the station's own thresholds (the card manual): frost
  below 0 °C, heat above 30 °C, twilight below 10 lx, calm below 0.3 m/s,
  strong wind above 14.9 m/s. Calm and strong wind are therefore both off in
  a moderate wind.
- Frost, heat and rain carry a sensor type (cold, heat, moisture); twilight,
  sunny, calm and strong wind do not, as no type's wording fits - "unsafe"
  for strong wind, or the light type, which would read the wrong way round
  at dusk.
- The station is read with the *Measurements* group (60 s by default). Rules
  that act on it - closing awnings in wind, say - belong in the central unit.
- Wind is taken as tenths of m/s, as the station sends one decimal place;
  so far only calm has been compared with the configurator.

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
| Lights | lights, dimmers, switches and analog outputs | 10 s |
| Measurements | thermometers, analog inputs, thermostats, the weather station | 60 s |

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

A command that works gets no reply: the card acknowledges it, and the central
unit speaks only to refuse it, in the answer to a following `get`. So after
switching an output or setting a dimmer's level the client polls `get` four
times, 50 ms apart, before taking silence for success. In 1404 measured
refusals - also with the remote panel open, the NexoVision app connected and a
gate sequence running - the refusal always came by the second poll. The wait
counts polls, not time: a busy central unit makes a command take longer but
does not lose its refusal. Fewer than three polls would risk reporting a
refused command as done. Logic commands, which return what the logic answers,
keep ten polls (0.5 s).

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
