# Setup

[← Back to the overview](../README.md)

## Adding an area

Settings → Devices & services → Add integration → **Home Timer**.

| Field | Description |
|---|---|
| Area | the Home Assistant area that gets timers, e.g. *Kitchen*. Each area can be set up only once. |
| Name | optional; prefix for the area's devices, e.g. *Kitchen: Hob 1*. Default: the area's name at setup time. Renaming the area later does not change it; rename the entry instead (the devices follow after the next restart or reload). |

Repeat this for every area that should have timers. An open kitchen-living room is one area; if you want separate timers for the kitchen part, give it its own Home Assistant area.

## The area's device

Every area gets a device **"&lt;name&gt;: Home Timer"**, placed in its area when it is created. The area's own entities (active timers, ringing, acknowledge all) will live on it. Afterwards its area is yours: move it to another area or take it out of any area, and Home Timer leaves it that way.

If the area is deleted, the entry keeps working and logs a warning; its device stays without an area. Delete the entry and set up the new area instead.

## Removing an area

Settings → Devices & services → Home Timer → the area's entry → ⋮ → **Delete**. Its device goes with it.
