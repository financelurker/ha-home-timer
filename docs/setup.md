# Setup

[← Back to the overview](../README.md)

## Adding an area

Settings → Devices & services → Add integration → **Home Timer**.

| Field | Description |
|---|---|
| Area | the Home Assistant area that gets timers, e.g. *Kitchen*. Each area can be set up only once. |
| Name | optional; prefix for the area's devices, e.g. *Kitchen: Hob 1*. Default: the area's name. |

Repeat this for every area that should have timers. An open kitchen-living room is one area; if you want separate timers for the kitchen part, give it its own Home Assistant area.

## The area's device

Every area gets a device **"&lt;name&gt;: Home Timer"** in its area. The area's own entities (active timers, ringing, acknowledge all) will live on it. Move it to another area if you like; Home Timer does not move it back.

## Removing an area

Settings → Devices & services → Home Timer → the area's entry → ⋮ → **Delete**. Its device goes with it.
