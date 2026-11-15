# Home Timer

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/financelurker/ha-home-timer)](https://github.com/financelurker/ha-home-timer/releases)
[![CI](https://github.com/financelurker/ha-home-timer/actions/workflows/ci.yml/badge.svg)](https://github.com/financelurker/ha-home-timer/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/financelurker/ha-home-timer)](https://github.com/financelurker/ha-home-timer/blob/main/LICENSE)

**Timers per room.** Home Timer gives every area its own timers: "Let dough rest, 30 minutes" in the kitchen, "Bath, 14 minutes" in the bathroom. When a timer runs out it rings until you acknowledge it, and your automations decide where to tell you – phone, speaker or light. Stations (a hob, the rice cooker, the bathtub) with presets follow.

> **Status: in development.** Areas, timers, actions and events work. Stations and presets come next.

## What it does

- **[One entry per area](https://github.com/financelurker/ha-home-timer/blob/main/docs/setup.md):** kitchen, bathroom, laundry – each area gets its own timers and a *Home Timer* device.
- **[Any number of timers](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md):** start, pause, resume, add or subtract time, cancel – by action, from a script or from another integration.
- **[Rings until acknowledged](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md#ringing):** overtime is counted, reminders repeat, and it stops by itself after a while.
- **[Survives restarts](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md#restarts):** a timer that ran out while Home Assistant restarted is reported right after the start.
- **[For your own automations](https://github.com/financelurker/ha-home-timer/blob/main/docs/automations.md):** events for every step, with the area, a note and your own data – e.g. a phone notification with "Got it" and "+2 min".
- **Stations** *(coming)*: hob 1, rice cooker, bathtub … each a device in the area, with one timer at a time.
- **Presets** *(coming)*: known durations per station, startable from a button.

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
3. Start a timer with the action `home_timer.start` (target: the area, a name and a duration) and react to `home_timer_finished` in an automation ([Automations](https://github.com/financelurker/ha-home-timer/blob/main/docs/automations.md)).

## Documentation

| Page | Content |
|---|---|
| [Setup](https://github.com/financelurker/ha-home-timer/blob/main/docs/setup.md) | adding an area, its device, options, removing it |
| [Timers](https://github.com/financelurker/ha-home-timer/blob/main/docs/timers.md) | life cycle, ringing, options, restarts |
| [Automations](https://github.com/financelurker/ha-home-timer/blob/main/docs/automations.md) | actions and events, with YAML examples |
| [Reference](https://github.com/financelurker/ha-home-timer/blob/main/docs/reference.md) | entities and attributes |

## Support

Questions, bugs and ideas: [GitHub issues](https://github.com/financelurker/ha-home-timer/issues). For a bug, please add the Home Assistant version and a debug log (Settings → Devices & services → Home Timer → ⋮ → **Enable debug logging**, reproduce it, then disable it again to download the log).

## License

MIT License, see [LICENSE](https://github.com/financelurker/ha-home-timer/blob/main/LICENSE).

This project is not affiliated with Home Assistant or the Open Home Foundation.
