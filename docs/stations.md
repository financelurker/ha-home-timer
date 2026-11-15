# Stations and presets

[← Back to the overview](../README.md)

A **station** is anything a timer belongs to: a hob, the rice cooker, the oven, the bathtub, the washing machine. Each station is its own device in the area, with **one timer at a time**, and has **presets** – known durations you start with one button.

## Adding a station

Settings → Devices & services → Home Timer → the area's entry → **Add station**.

1. **Name and icon:** e.g. *Hob 1* with `mdi:stove`. The dialog keeps names unique within the area (renaming a station with ⋮ → *Rename* does not check this; Home Timer logs a warning if two stations share a name). With an icon, the station's timer shows it in every state instead of the state icons.
2. **Presets** (a menu, as often as you like):
   - **Add preset:** name (*Rice*), duration (10 min), optional note (*level 6, then 3*). The note goes with the timer's events, e.g. into a notification.
   - **Change or delete a preset.**
3. **Done** saves the station. The area reloads; running timers go on.

Change a station later with **Configure** next to it on the entry; remove it with ⋮ → **Delete**. A timer that runs on a deleted station is cancelled (`reason: station_removed`).

How you set up your stations is up to you – by place (*Hob 1*, *Hob 2*) with dishes as presets, or by appliance (*Rice cooker* with *White rice*, *Brown rice*). One station, one timer: for two timers on the same spot, add a second station.

## The station's device

The station's device carries its name and lies in the area (move it if you like). Its entities, e.g. for *Hob 1* in the kitchen (entity ids come from the names in your system language when the entities are created; the examples are for an English system – on a German one e.g. `button.kuche_kochfeld_1_reis_starten`):

| Entity | Description |
|---|---|
| `sensor.kitchen_hob_1` | the station's timer: *idle*, *active*, *paused* or *ringing*; details as attributes (see [Reference](reference.md#station)) |
| `sensor.kitchen_hob_1_timer_end` | when the timer runs out; the frontend shows "in 6 minutes" |
| `button.kitchen_hob_1_start_rice` | one button per preset: starts it (replacing the station's timer) |
| `button.kitchen_hob_1_pause_resume` | pauses or resumes the timer |
| `button.kitchen_hob_1_1_minute` | +1 minute; a ringing timer starts again with one minute |
| `button.kitchen_hob_1_stop_acknowledge` | acknowledges a ringing timer, cancels a running or paused one |

Renaming a preset keeps its button (its entity id stays); deleting a preset removes it.

## One timer per station

Starting a timer on a station that already has one replaces it: the old timer fires `home_timer_cancelled` with `reason: replaced`. Timers without a station (started on the area) are not affected; an area can have any number of those.

## From automations and scripts

Target the station's device or one of its entities:

```yaml
action: home_timer.start
target:
  entity_id: sensor.kitchen_hob_1
data:
  preset: Rice            # or: name + duration
```

`note` replaces the preset's note; a preset brings its own duration, so `duration` is refused with a preset. Every event of a station's timer carries `station`, `station_name` and the station's `device_id`; device triggers on the station's device fire for its timer only. See [Automations](automations.md).
