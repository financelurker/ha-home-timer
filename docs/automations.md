# Automations

[← Back to the overview](../README.md)

## Actions

Start a timer with `home_timer.start`:

- **On a station:** target the station's device or one of its entities, and give a `preset` (its own duration; `note` replaces the preset's note) – or a `name` and a `duration`. It replaces the station's timer.
- **On an area, without a station:** target the area – or anything that leads to exactly one area set up in Home Timer (its *Home Timer* device, a floor, a label) – with a `preset` (a quick timer of the area) or a `name` and a `duration`.

```yaml
action: home_timer.start
target:
  entity_id: sensor.kitchen_hob_1
data:
  preset: Rice
```

```yaml
action: home_timer.start
target:
  area_id: kitchen
data:
  name: Let dough rest
  duration: "00:30:00"
  note: Cover with a towel        # optional, goes with every event of the timer
  source:                          # optional, your own data, returned in every event
    my_script: {step: 2}
response_variable: started         # optional: started.timer.id is the timer's id
```

The other actions take the timer's id – from the response above, from an event (`timer_id`) or from a sensor's attributes – or target stations (their devices or entities), meaning the station's timer:

| Action | Data | Description |
|---|---|---|
| `home_timer.pause` | `timer_id` or stations | pauses an active timer |
| `home_timer.resume` | `timer_id` or stations | resumes a paused timer |
| `home_timer.add_time` | `timer_id` or stations, `duration` | extends a timer; a ringing timer starts again |
| `home_timer.subtract_time` | `timer_id` or stations, `duration` | shortens a timer; down to zero it runs out |
| `home_timer.cancel` | `timer_id` or stations | cancels a timer, whatever its state |
| `home_timer.acknowledge` | `timer_id`, stations, or areas (area, floor, …) | acknowledges one ringing timer, the ringing timers of the stations, or all ringing timers of the areas |
| `home_timer.assign` | `timer_id`, one station of the timer's area | attaches the timer to the station: it keeps its state and time; a timer the station has is replaced; a timer of another station moves (response: the timer) |

A target names either stations or areas, not both (only `acknowledge` takes both). With a `timer_id`, the target is not used (except by `assign`, which needs both). Durations must be at least one second. An action that does not fit the timer's state (pause a paused timer, acknowledge one that does not ring) fails with a message saying why.

## Events

| Event | When | Extra data |
|---|---|---|
| `home_timer_started` | a timer starts | |
| `home_timer_paused` | it is paused | |
| `home_timer_resumed` | it is resumed | |
| `home_timer_time_changed` | time is added or subtracted | `change` (seconds, negative when subtracted; at most what was left) |
| `home_timer_finished` | it runs out | `late` (`true`: it ran out during a restart); for [power stations](power.md) `trigger`: `rule` or `off` |
| `home_timer_reminder` | while it rings, every few minutes | `overtime` (seconds since it ran out) |
| `home_timer_cancelled` | it is cancelled | `reason`: `manual`, `replaced` (a new or assigned timer on the station), `power_off` (a power station's appliance was switched off) or `station_removed` (then `device_id` is the area's device; `station_name` is still the station's) |
| `home_timer_acknowledged` | a ringing timer is acknowledged | `by`: `button`, `switch` (a quick timer's or the custom timer's switch turned off), `action`, `todo` (checked off in a to-do list), `timeout` or `power` (a power station switched off); `overtime` |
| `home_timer_assigned` | it is attached to a station (`home_timer.assign`) | `previous_station`, `previous_station_name` (when it moved from another station, else empty) |

Every event carries:

```yaml
timer_id: 01JD7Q…
name: Let dough rest
area_id: kitchen
entry_id: 01JC…             # the area's config entry
device_id: a1b2…            # the station's device, or the area's Home Timer device
station: 01JD…              # the station's id, or null without a station
station_name: Hob 1
preset: Rice                # the preset's name, or null
duration: 1800              # seconds, including added or subtracted time
note: Cover with a towel
source: {my_script: {step: 2}}
```

## Device triggers

In the automation editor: **When** → *Device* → a station's device or the area's *Home Timer* device:

| Trigger | Station's device | Area's *Home Timer* device |
|---|---|---|
| Timer started | its timer | any timer of the area |
| Timer assigned to the station | a timer attached to it | any timer attached to a station of the area |
| Timer finished | its timer | any timer of the area |
| Timer reminds (still ringing) | its timer | any timer of the area |
| Timer acknowledged | its timer | any timer of the area |

The trigger variables are those of the event: `trigger.event.data.name`, `.station_name`, `.note` …

## Example: notify the phone, with "Got it" and "+2 min"

```yaml
triggers:
  - trigger: event
    event_type: home_timer_finished
  - trigger: event
    event_type: home_timer_reminder
actions:
  - action: notify.mobile_app_phone
    data:
      title: "⏱ {{ trigger.event.data.name }}{{ ' · ' ~ trigger.event.data.station_name if trigger.event.data.station_name else '' }}"
      message: >-
        {% if trigger.event.event_type == 'home_timer_reminder' %}
          Done {{ (trigger.event.data.overtime / 60) | round | int }} min ago.
        {% else %}
          Done{{ ' (during a restart)' if trigger.event.data.get('late') else '' }}.
        {% endif %}
        {{ trigger.event.data.note or '' }}
      data:
        tag: "home_timer_{{ trigger.event.data.timer_id }}"
        actions:
          - action: "HT_ACK_{{ trigger.event.data.timer_id }}"
            title: Got it
          - action: "HT_PLUS2_{{ trigger.event.data.timer_id }}"
            title: "+2 min"
```

And the answer from the notification:

```yaml
triggers:
  - trigger: event
    event_type: mobile_app_notification_action
actions:
  - variables:
      action: "{{ trigger.event.data.action }}"
  - choose:
      - conditions: "{{ action.startswith('HT_ACK_') }}"
        sequence:
          - action: home_timer.acknowledge
            data:
              timer_id: "{{ action[7:] }}"
      - conditions: "{{ action.startswith('HT_PLUS2_') }}"
        sequence:
          - action: home_timer.add_time
            data:
              timer_id: "{{ action[9:] }}"
              duration: "00:02:00"
```

Only for one area: add a condition `{{ trigger.event.data.area_id == 'kitchen' }}`.
