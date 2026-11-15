# Reference

[← Back to the overview](../README.md)

`<area>` is the area's part of the entity id, e.g. `kitchen`; `<station>` the station's, e.g. `hob_1`. Entity ids come from the names in the system language when the entities are created; the suffixes below are those of an English system.

## Area

On the area's *Home Timer* device.

| Entity | Description |
|---|---|
| `sensor.<area>_home_timer_timers` | *Timers*: how many timers the area has (active, paused or ringing) |
| `binary_sensor.<area>_home_timer_ringing` | on while any timer of the area rings |
| `button.<area>_home_timer_acknowledge_all` | acknowledges all ringing timers of the area (does nothing while none rings) |

## Attributes of *Timers*

| Attribute | Description |
|---|---|
| `ringing` | *Ringing timers*: how many timers ring |
| `next_end` | when the next active timer runs out (ISO timestamp), or empty |
| `next_name` | that timer's name |
| `timers` | all timers, ringing first, then by remaining time (not recorded in the history) |

Each entry of `timers`:

| Field | Description |
|---|---|
| `id` | the timer's id, for the actions |
| `name` | the timer's name |
| `status` | `active`, `paused` or `ringing` |
| `duration` | seconds in total, including added or subtracted time |
| `remaining` | seconds left when the state was written (use `ends_at` for a live countdown) |
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
| `sensor.<area>_<station>_timer_end` | when the timer runs out (timestamp, while active) |
| `button.<area>_<station>_start_<preset>` | starts the preset (one per preset) |
| `button.<area>_<station>_pause_resume` | pauses or resumes the timer |
| `button.<area>_<station>_1_minute` | +1 minute (a ringing timer starts again) |
| `button.<area>_<station>_stop_acknowledge` | acknowledges a ringing timer, cancels a running or paused one |

Attributes of `sensor.<area>_<station>`:

| Attribute | Description |
|---|---|
| `presets` | the station's preset names (always) |
| `timer_id`, `timer_name`, `preset` | the timer, while there is one |
| `duration`, `remaining`, `overtime` | seconds (`remaining` and `overtime` when the state was written; not recorded) |
| `started_at`, `ends_at`, `finished_at` | ISO timestamps |
| `note`, `source` | as given at the start (`source` is not recorded) |

Actions, events and device triggers: see [Automations](automations.md). States and options: see [Timers](timers.md).
