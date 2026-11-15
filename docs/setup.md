# Setup

[← Back to the overview](../README.md)

## Adding an area

Settings → Devices & services → Add integration → **Home Timer**, then pick the Home Assistant area that gets timers, e.g. *Kitchen*. Each area can be set up only once; the entry is named after the area.

Repeat this for every area that should have timers. An open kitchen-living room is one area; if you want separate timers for the kitchen part, give it its own Home Assistant area.

## The area's device

Every area gets a device **"Home Timer"**, placed in its area when it is created. It carries the area's entities (see [Reference](reference.md)): *Timers*, *Ringing*, *Acknowledge all* and the to-do list *Ringing timers*.

The device name does not repeat the area: Home Assistant shows the area next to it and puts the area into entity ids, e.g. `sensor.kitchen_home_timer_timers`. (Up to 0.0.1 the device was called "Kitchen: Home Timer"; it is renamed by itself.)

After it was created, its area is yours: move it to another area or take it out of any area, and Home Timer leaves it that way.

If the area is deleted, the entry keeps working and logs a warning; its device stays without an area. Delete the entry and set up the new area instead.

## Stations

Stations (hob, rice cooker, bathtub …) are added on the area's entry with **Add station**; each gets its own device. See [Stations and presets](stations.md).

## Options

Settings → Devices & services → Home Timer → the area's entry → **Configure** sets how the area's timers behave when they run out: ringing, reminders, auto-dismiss and timers missed during a restart. See [Timers](timers.md#options).

## Removing an area

Settings → Devices & services → Home Timer → the area's entry → ⋮ → **Delete**. Its devices (the area's and its stations') and its stored timers go with it.
