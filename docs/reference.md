# Reference

[← Back to the overview](../README.md)

`<area>` is the area's part of the entity id, e.g. `kitchen`; `<station>` the station's, e.g. `hob_1`. Entity ids come from the names in the system language when the entities are created; the suffixes below are those of an English system.

## Area

On the area's *Home Timer* device.

| Entity | Description |
|---|---|
| `sensor.<area>_home_timer_timers` | *Timers*: how many timers the area has (active, paused or ringing) |
| `binary_sensor.<area>_home_timer_ringing` | *Ringing* while any timer of the area rings, else *Quiet* – device class *problem*, so Home Assistant shows it red while it rings |
| `button.<area>_home_timer_acknowledge_all` | acknowledges all ringing timers of the area (does nothing while none rings) |
| `button.<area>_home_timer_start_<quick timer>` | starts the quick timer (one per quick timer of the area) |
| `number.<area>_home_timer_custom_timer` | custom duration in minutes (1–600) |
| `button.<area>_home_timer_start_custom_timer` | starts a timer "Timer" with the custom duration |
| `text.<area>_home_timer_save_as_preset` | enter a name: saves the last custom timer (or the custom duration) as a quick timer; attributes `custom_timer`, `duration` |

## Attributes of *Timers*

| Attribute | Description |
|---|---|
| `ringing` | *Ringing timers*: how many timers ring |
| `next_end` | when the next active timer runs out (ISO timestamp), or empty |
| `next_name` | that timer's name |
| `timers` | all timers, ringing first, then by remaining time, open timers last (not recorded in the history) |

Each entry of `timers`:

| Field | Description |
|---|---|
| `id` | the timer's id, for the actions |
| `name` | the timer's name |
| `status` | `active`, `paused` or `ringing` |
| `duration` | seconds in total, including added or subtracted time |
| `remaining` | seconds left when the state was written (use `ends_at` for a live countdown); empty for an open timer |
| `elapsed` | seconds since it started |
| `open` | `true` for a running timer of a [power station](power.md) without an end (it finishes when the appliance is done) |
| `started_at` / `ends_at` / `finished_at` | ISO timestamps; `ends_at` while active, `finished_at` while ringing |
| `overtime` | seconds since it ran out (ringing) |
| `note`, `source` | as given at the start |
| `station`, `preset` | the station's id and the preset's name, or empty |

## Home Timer as a whole

| Entity | Description |
|---|---|
| `todo.home_timer_ringing_timers` | one to-do list for all areas: the ringing timers; checking one off acknowledges it (see [Timers](timers.md#to-do-list)) |

## Station

On the station's device.

| Entity | Description |
|---|---|
| `sensor.<area>_<station>` | the station's timer: `idle`, `active`, `paused`, `ringing` |
| `binary_sensor.<area>_<station>_ringing` | *Ringing* while the station's timer rings, else *Quiet* – shown red while it rings (device class *problem*) |
| `sensor.<area>_<station>_timer_end` | when the timer runs out (timestamp, while active); for an open timer the expected end |
| `button.<area>_<station>_start_<preset>` | starts the preset (one per preset) |
| `button.<area>_<station>_pause_resume` | pauses or resumes the timer |
| `button.<area>_<station>_1_minute` | +1 minute (a ringing timer starts again) |
| `button.<area>_<station>_stop_acknowledge` | acknowledges a ringing timer, cancels a running or paused one |
| `number.<area>_<station>_custom_timer` | custom duration in minutes (1–600) |
| `button.<area>_<station>_start_custom_timer` | starts a timer "Timer" with the custom duration |
| `text.<area>_<station>_save_as_preset` | enter a name: saves the custom timer as a preset of the station; attributes `custom_timer`, `duration` |

The preset, custom timer, pause and +1 minute entities exist on manual stations and on power stations that start a preset. [Power stations](power.md) also have:

| Entity | Description |
|---|---|
| `binary_sensor.<area>_<station>_running` | the appliance runs |
| `sensor.<area>_<station>_runs` | how often it ran (total increasing) |
| `sensor.<area>_<station>_usual_duration` | median duration of the last 20 runs; attributes `last`, `durations` |

Attributes of `sensor.<area>_<station>`:

| Attribute | Description |
|---|---|
| `presets` | the station's preset names (always) |
| `timer_id`, `timer_name`, `preset` | the timer, while there is one |
| `duration`, `remaining`, `overtime` | seconds (`remaining` and `overtime` when the state was written; not recorded) |
| `started_at`, `ends_at`, `finished_at` | ISO timestamps |
| `note`, `source` | as given at the start (`source` is not recorded) |
| `elapsed` | seconds since it started (not recorded) |
| `by_power`, `running`, `power_sensor`, `done_rule` | power stations: the kind (`until_off`, `start_preset`, `warn`), whether the appliance runs, its sensor, the done rule in words (`until_off` only) |

Actions, events and device triggers: see [Automations](automations.md). States and options: see [Timers](timers.md).
