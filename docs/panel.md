# The Home Timer panel

[← Back to the overview](../README.md)

Areas, stations, presets and options are set up in Home Timer's own panel – not in dialogs. It is for administrators and works on the phone as well.

![The panel: every station in a bar at the top, the overview with their live state below](images/panel.png)

## Opening it

- **The cog of an area:** Settings → Devices & services → Home Timer → the cog next to an area opens the panel at that area's card (*Ringing & reminders*).
- **A device's link:** on the page of an area's *Home Timer* device or of a station's device, the configuration link (*Visit*) opens the panel there.
- **After adding an area:** the confirmation links to the panel.
- **The sidebar (optional):** the panel is hidden from the sidebar by default. Turn it on in the panel: ⋮ at the top right → *Panel settings* → *Show in the sidebar* (for all areas and users with admin rights).

Stations have no cog and no "Add station" on the integration page: they are added and changed in the panel. They are still subentries of the area's entry, with their own devices and entities; their ⋮ menu there still offers *Rename* (without the panel's check for unique names) and *Delete*.

The panel exists while Home Timer is loaded, i.e. while at least one area is set up. After removing the last area and restarting, add the first area again with Settings → Devices & services → Add integration → **Home Timer**.

## Layout

A bar at the top shows **Overview** and then every station of every area, A–Z, each with what it does right now (a red dot and the overtime while it rings, an orange dot and the time while it runs). One click opens a station; **+ Station** adds one (with more than one area, it asks for the area first). The bar and the tabs stay at the top while the page scrolls; on a phone the bar scrolls sideways. The header shows how many timers ring in the whole house.

The overview has three tabs:

- **Overview:** what rings right now, with *Acknowledge*; then every station as a card – ringing and running ones first, then A–Z – with its area, what it does and what it does right now (timer and remaining time, ringing, the appliance's power). Timers running without a station are listed below. The panel shows this live, but besides *Acknowledge* it does not operate timers – that is what dashboards, the devices' buttons and the actions are for.
- **Quick timers:** the quick timers of all areas in one list – presets without a station: name, duration, optional note and (with more than one area) the area. Sort them by dragging or with the arrows.
- **Ringing & reminders:** one card per area with how its timers ring – ring until acknowledged, reminder, stop ringing after, report missed timers (see [Timers](timers.md#options)) – links to its device and entities, and **Remove**, which deletes the area's entry with its stations, devices and stored timers. **+ Add area** offers Home Assistant's areas that have no Home Timer yet.

Areas stay what they are in Home Assistant – one entry per area with its devices and entities; the panel just does not group the stations by them.

The ⋮ menu at the top right holds the panel settings, a link to this documentation and – on a station – deleting it.

## Stations

**+ Station** in the bar, or a station's chip or card, opens the station. A saved station has three tabs; a new one shows only its settings until it is saved.

**A station by hand:**

- **Overview:** what it does right now (with *+1 min* and *Acknowledge* while it rings, *Pause*, *Resume* and *Stop* while it runs), links to its device, entities and history, and a start button per preset.
- **Presets:** name, duration (`9` = 9 minutes, `9:30`, `1:05:00`), optional note. Sort them by dragging or with the arrows (on a phone, note and delete are behind ⋯); the buttons follow. Renaming a preset keeps its button.
- **Settings:** the area it is in, name, icon (Home Assistant's icon picker) and how it **starts**: *By hand* – presets and buttons, see [Stations and presets](stations.md) – or *By power* – a power sensor drives the timer, see [Power stations](power.md) and [below](#a-station-by-power). Names are unique within the area. **Delete** removes the station; a timer running on it is cancelled (`reason: station_removed`).

![A station by hand: its presets, sortable](images/panel-station.png)

On a phone:

<img src="images/phone-area.png" alt="The overview on a phone" width="300">

## A station by power

- **Overview:** what it does right now (timer, the appliance on or off, its power, how long it usually takes), links to its device, entities and history, and its last runs with your marks. When suggestions from your runs are ready, a card leads to them.
- **Runs:** the list of runs on the left, the chosen run's curve on the right, and under it **It is done when …** – everything a change needs to show at once what it does with the station's real runs (the **preview**).
  - **The curve** of the chosen run. Marked in it: the *runs above* threshold (dashed), the start the done rule ignores (grey), where the run stopped (*off*), and when it rings – a solid line for the saved settings, a dashed one for your changes while they are not saved. For *the power falls by* and *the trend bends …*, the rule's window is shaded; with steps, numbered pins show where each step was met; for *pauses* and *stops cycling*, its level is drawn. Point at the curve to read a value. **Zoom** into a run longer than a minute with 🔍 (or Ctrl + wheel, a touchpad pinch, or + − 0 on the focused chart) – zooming in keeps your mark in view. Zoomed in, the power scale fits the part shown (↕ gives the whole range back), and an overview of the whole run below the chart marks the part shown: drag it to move along (or use a sideways wheel, or ← → when not marking); drag its edges to make it wider or narrower. A run going on right now is shown live.
  - **The runs:** newest first, with duration and – with marks – *on time*, *too early* or *too late*; click one to see its curve. The history icon reads a run again from the recorder (its real start and whole curve – see [Runs](power.md#runs)); the bin icon deletes a run (it asks first). Runs read from the history, or recorded with another sensor, are marked.
  - **It is done when …** (for *Ring when it's done*): the done rule as a sentence, e.g. *the power falls by at least 10 % within 60 s – ignore the first 120 s*; ⓘ explains all rules. **+ Add a step** turns it into steps, *it is done after these steps*, joined by *then*: move them with ▲ ▼ or by dragging, remove them with × (see [Power stations › Steps](power.md#steps)). While you change it, a summary says what your draft does on all stored runs – e.g. *3 done earlier (median 12 s) · 5 unchanged* – and which runs it would never call done.
- **Settings:** the area, name, icon and how it starts; **What should the timer do?** – *Ring when it's done* (its rule is on *Runs*), *Start a preset* (with the presets) or *Warn if it runs too long*; **On and off** – the power sensor and, as a sentence, when the appliance runs and counts as off; the **sensor**: how often it reports and how much its readings scatter; **Delete**.

**Learning:** mark in a few runs when the appliance was really done – click the curve, drag the mark, or *Done now* while it runs – and *Suggest from my runs* offers rules that fit (see [Power stations › Learning](power.md#learning)). A run whose mark looks off is shown with *Check the mark*, *Leave this run out* and *Keep it*. A new station shows its runs once it is saved. On a phone, the runs tab shows the curve first, then the rule, then the runs.

<img src="images/phone-power.png" alt="The moka pot's runs on a phone: its curve" width="300"> <img src="images/phone-runs.png" alt="The rule and the runs on a phone" width="300">

## Saving

Changes collect in a bar at the bottom: **Save** or **Discard**; a dot marks each tab with changes, and switching tabs keeps them (*Quick timers* and *Ringing & reminders* share theirs; so do a station's tabs). After a failed save the panel shows the tab with the errors. Saving reloads each changed area once; running timers go on. Leaving the page within the panel, reloading or closing the tab with unsaved changes asks first; leaving it otherwise (browser back, the sidebar, a link) discards them.

Errors are shown next to their field, and nothing is saved until they are fixed. If the area or station was changed elsewhere in the meantime – e.g. *Save as preset* on a dashboard – the save is refused and the current state is loaded, so nothing is overwritten.
