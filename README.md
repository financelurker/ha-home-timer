# Home Timer

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/financelurker/ha-home-timer)](https://github.com/financelurker/ha-home-timer/releases)
[![CI](https://github.com/financelurker/ha-home-timer/actions/workflows/ci.yml/badge.svg)](https://github.com/financelurker/ha-home-timer/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/financelurker/ha-home-timer)](https://github.com/financelurker/ha-home-timer/blob/main/LICENSE)

**Timers per room.** Home Timer gives every area its own timers. A timer can belong to a **station** – a hob, the rice cooker, the bathtub – and stations have **presets**: "Rice" is 10 minutes, "Fill the bathtub" 14, one button each. When a timer runs out it rings until you acknowledge it, and your automations decide where to tell you – phone, speaker or light.

## What it does

- **[One entry per area](https://github.com/financelurker/ha-home-timer/blob/main/docs/setup.md):** kitchen, bathroom, laundry – each area gets its own timers and a *Home Timer* device.
- **[Timers without a station](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md):** any number per area – start, pause, resume, add or subtract time, cancel – by action, from a script or from another integration; [attach one to a station](https://github.com/financelurker/ha-home-timer/blob/main/docs/stations.md#attaching-a-timer-later) later.
- **[Rings until acknowledged](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md#ringing):** overtime is counted, reminders repeat, and it stops by itself after a while.
- **[One to-do list](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md#to-do-list):** ringing timers of all areas show up as open to-dos ("Rice · Hob 1 · Kitchen"); checking one off acknowledges it.
- **[Survives restarts](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md#restarts):** a timer that ran out while Home Assistant restarted is reported right after the start.
- **[For your own automations](https://github.com/financelurker/ha-home-timer/blob/main/docs/automations.md):** events for every step, with the area, a note and your own data – e.g. a phone notification with "Got it" and "+2 min".
- **[Stations](https://github.com/financelurker/ha-home-timer/blob/main/docs/stations.md):** hob 1, rice cooker, bathtub … each a device in the area with one timer at a time, its state and end as sensors, and buttons for pause, +1 minute and stop.
- **[Presets](https://github.com/financelurker/ha-home-timer/blob/main/docs/stations.md#adding-a-station):** known durations per station, one button each – no card needed; [created while cooking](https://github.com/financelurker/ha-home-timer/blob/main/docs/stations.md#creating-presets-while-cooking) from a custom timer.
- **[Dashboard controls](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md#starting-timers-from-a-dashboard)** for timers without a station: quick timers (one tap), a custom duration, "save as preset".
- **[Power stations](https://github.com/financelurker/ha-home-timer/blob/main/docs/power.md):** a power sensor starts the timer – it rings when the appliance is done (e.g. a moka pot), starts a preset when switched on, or warns if it runs too long.
- **[Device triggers](https://github.com/financelurker/ha-home-timer/blob/main/docs/automations.md#device-triggers)** per station and per area: started, finished, reminder, acknowledged.

## Installation

Requires Home Assistant 2026.11 or newer.

### HACS

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=financelurker&repository=ha-home-timer&category=integration)

1. Click the button above. It opens the repository in HACS and adds it as a custom repository if needed.
2. Install **Home Timer** and restart Home Assistant.

Without the button: HACS → ⋮ → **Custom repositories** → add `https://github.com/financelurker/ha-home-timer` as type *Integration*.

### Manual

1. Copy `custom_components/home_timer` into `config/custom_components/`.
2. Restart Home Assistant.

## Getting started

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=home_timer)

1. Settings → Devices & services → Add integration → **Home Timer**.
2. Pick an area (e.g. Kitchen). Repeat for every area that should have timers ([Setup](https://github.com/financelurker/ha-home-timer/blob/main/docs/setup.md)).
3. On the area's entry: **Add station** (e.g. *Hob 1*) with presets (e.g. *Rice*, 10 min) – [Stations](https://github.com/financelurker/ha-home-timer/blob/main/docs/stations.md).
4. Press *Start Rice* on the station's device, and react to `home_timer_finished` (or the station's device trigger) in an automation – [Automations](https://github.com/financelurker/ha-home-timer/blob/main/docs/automations.md).

## Documentation

| Page | Content |
|---|---|
| [Setup](https://github.com/financelurker/ha-home-timer/blob/main/docs/setup.md) | adding an area, its device, options, removing it |
| [Stations and presets](https://github.com/financelurker/ha-home-timer/blob/main/docs/stations.md) | adding a station, presets, its device and entities, one timer per station |
| [Power stations](https://github.com/financelurker/ha-home-timer/blob/main/docs/power.md) | stations started by a power sensor: kinds, done rules, entities |
| [Timers](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md) | dashboard controls and quick timers, life cycle, ringing, to-do list, options, restarts |
| [Automations](https://github.com/financelurker/ha-home-timer/blob/main/docs/automations.md) | actions, events and device triggers, with YAML examples |
| [Reference](https://github.com/financelurker/ha-home-timer/blob/main/docs/reference.md) | entities and attributes |

## Support

Questions, bugs and ideas: [GitHub issues](https://github.com/financelurker/ha-home-timer/issues). For a bug, please add the Home Assistant version and a debug log (Settings → Devices & services → Home Timer → ⋮ → **Enable debug logging**, reproduce it, then disable it again to download the log).

## License

MIT License, see [LICENSE](https://github.com/financelurker/ha-home-timer/blob/main/LICENSE).

This project is not affiliated with Home Assistant or the Open Home Foundation.
