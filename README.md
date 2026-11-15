# Home Timer

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/financelurker/ha-home-timer)](https://github.com/financelurker/ha-home-timer/releases)
[![CI](https://github.com/financelurker/ha-home-timer/actions/workflows/ci.yml/badge.svg)](https://github.com/financelurker/ha-home-timer/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/financelurker/ha-home-timer)](https://github.com/financelurker/ha-home-timer/blob/main/LICENSE)

**Timers per room.** Home Timer will give every area its own timers. A timer can belong to a **station** – a hob, the rice cooker, the bathtub, the washing machine – and stations have **presets**: "Rice" is 10 minutes, "Fill the bathtub" 14. When a timer runs out it will ring until you acknowledge it, and your automations decide where to tell you.

> **Status: in development.** This version sets up areas. The timer engine, stations, presets and entities follow in the next releases.

## What it does

- **[One entry per area](https://github.com/financelurker/ha-home-timer/blob/main/docs/setup.md):** kitchen, bathroom, laundry – each area gets its own timers.
- **Stations** *(coming)*: hob 1, rice cooker, bathtub … each a device in the area, with one timer at a time.
- **Presets** *(coming)*: known durations per station, startable from a button, an action or a script.
- **Timers without a station** *(coming)*: any number per area, e.g. started by a script or another integration.
- **Rings until acknowledged** *(coming)*: overtime is counted, reminders repeat, and it stops by itself after a while.
- **Survives restarts** *(coming)*: a timer that ran out while Home Assistant restarted is reported right after the start.
- **For your own automations** *(coming)*: actions, events and device triggers – notify your phone, a speaker or a light.

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

## Documentation

| Page | Content |
|---|---|
| [Setup](https://github.com/financelurker/ha-home-timer/blob/main/docs/setup.md) | adding an area, its name and device |

## Support

Questions, bugs and ideas: [GitHub issues](https://github.com/financelurker/ha-home-timer/issues). For a bug, please add the Home Assistant version and a debug log (Settings → Devices & services → Home Timer → ⋮ → **Enable debug logging**, reproduce it, then disable it again to download the log).

## License

MIT License, see [LICENSE](https://github.com/financelurker/ha-home-timer/blob/main/LICENSE).

This project is not affiliated with Home Assistant or the Open Home Foundation.
