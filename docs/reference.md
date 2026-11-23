# Reference

[← Back to the overview](../README.md)

`<area>` is the area's part of the entity id, e.g. `kitchen`; `<station>` the station's, e.g. `hob_1`. Entity ids come from the names in the system language when the entities are created; the suffixes below are those of an English system.

## Area

On the area's *Home Timer* device.

| Entity | Description |
|---|---|
| `sensor.<area>_home_timer_timers` | *Timers*: how many timers the area has (active, paused or ringing) |
| `binary_sensor.<area>_home_timer_ringing` | *Ringing* while a timer of the area without a station rings, else *Quiet* – device class *problem*, so Home Assistant shows it red while it rings. A station's timer shows on the station's own *Ringing* sensor only. |
| `button.<area>_home_timer_stop_ringing` | *Stop ringing*: acknowledges the area's ringing timers without a station – stations have their own *Stop* button. Available only while one of them rings. The `home_timer.acknowledge` action on the area still acknowledges all of them. |
| `switch.<area>_home_timer_<quick timer>` | one per quick timer: on while its timer runs, is paused or rings; on starts it, off stops it (or acknowledges it while it rings). Attributes `status`, `timer_id` while on |
| `sensor.<area>_home_timer_<quick timer>_ends` | when that quick timer's timer runs out (timestamp) – or ran out, while it rings |
| `number.<area>_home_timer_custom_timer_minutes` | custom duration in minutes (1–600) |
| `switch.<area>_home_timer_custom_timer` | on starts a timer "Timer" with the custom duration, off stops it (or acknowledges it while it rings) |
| `sensor.<area>_home_timer_custom_timer_ends` | when that timer runs out – or ran out, while it rings |
| `text.<area>_home_timer_save_as_preset` | enter a name: saves the last custom timer (or the custom duration) as a quick timer; attributes `custom_timer`, `duration` |

Also per area, but not on its device: `todo.<area>_timers` (*Home Timer: Kitchen timers*) – all timers of the area, see [Timers › To-do lists](timers.md#to-do-lists). Areas set up earlier keep `number.<area>_home_timer_custom_timer` (only its name changed to *Custom timer minutes*).

## Attributes of *Timers*

| Attribute | Description |
|---|---|
| `ringing` | *Ringing timers*: how many timers of the area ring, stations' included |
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
| `todo.home_timer_ringing_timers` | one to-do list for all areas: the ringing timers; checking one off acknowledges it (see [Timers](timers.md#to-do-lists)) |

## Station

On the station's device.

| Entity | Description |
|---|---|
| `sensor.<area>_<station>` | the station's timer: `idle`, `active`, `paused`, `ringing` |
| `binary_sensor.<area>_<station>_ringing` | *Ringing* while the station's timer rings, else *Quiet* – shown red while it rings (device class *problem*). With *Ring until acknowledged* off a timer never rings, so it stays *Quiet* |
| `sensor.<area>_<station>_timer_end` | when the timer runs out (timestamp, while active); for an open timer the expected end |
| `switch.<area>_<station>_<preset>` | one per preset: on while the station's timer is this preset's (at most one is on); on starts it, replacing the station's timer; off stops it – or acknowledges it while it rings |
| `button.<area>_<station>_pause_resume` | pauses or resumes the timer – available while it runs (with an end) or is paused |
| `button.<area>_<station>_1_minute` | +1 minute (a ringing timer starts again) – available while the station has a timer that has an end |
| `button.<area>_<station>_stop` | *Stop*: acknowledges a ringing timer, cancels a running or paused one – available while the station has a timer |
| `button.<area>_<station>_done_now` | power stations that ring when done: marks the run going on as done (learning) – available while the appliance runs |

| `number.<area>_<station>_custom_timer_minutes` | *Custom timer minutes*: custom duration in minutes (1–600); stations set up earlier keep `…_custom_timer` |
| `switch.<area>_<station>_custom_timer` | *Custom timer*: on starts a timer "Timer" with the custom duration, off stops it |
| `text.<area>_<station>_save_as_preset` | enter a name: saves the custom timer as a preset of the station; attributes `custom_timer`, `duration` |

Buttons that act on a timer are *unavailable* while they could do nothing, so a dashboard only offers what makes sense right now. Pressing an unavailable button does nothing (Home Assistant logs a warning) – in automations, use the `home_timer.*` actions or check the state first. Stations and areas set up earlier keep their entity ids (`…_acknowledge_all`, `…_stop_acknowledge`); only the names changed.

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
| `by_power`, `running`, `power_sensor`, `done_rule`, `steps_met` | power stations: the kind (`until_off`, `start_preset`, `warn`), whether the appliance runs, its sensor, the done rule in words (`until_off` only), how many of its steps are met in the run going on (with more than one step) |

Actions, events and device triggers: see [Automations](automations.md). States and options: see [Timers](timers.md).
