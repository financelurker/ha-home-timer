# Reference

[← Back to the overview](../README.md)

Entities of an area, on its *Home Timer* device. `<area>` is the area's part of the entity id, e.g. `kitchen`.

## Entities

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
| `station`, `preset` | empty for now; stations and presets follow in a later release |

Actions and events: see [Automations](automations.md). States and options: see [Timers](timers.md).
