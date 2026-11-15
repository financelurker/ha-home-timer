# Timers

[← Back to the overview](../README.md)

A timer belongs to an area. It has a name ("Let dough rest"), a duration, and optionally a note and source data of whoever started it. An area can have any number of timers at once.

## Life cycle

```
active ⇄ paused → ringing → acknowledged (or auto-dismissed)
   └──── cancelled ────┘
```

| State | Meaning |
|---|---|
| active | counting down |
| paused | stopped, keeps its remaining time |
| ringing | ran out, waits to be acknowledged; counts its overtime |

Cancelled and acknowledged timers are gone; an area without timers has nothing to show.

- **Add time** to an active or paused timer extends it; to a ringing timer, it starts it again with that time ("two more minutes").
- **Subtract time** shortens it; down to zero (also when paused) it runs out right away.
- Every change fires an [event](automations.md#events).

## Ringing

Home Timer makes no sound itself. "Ringing" is a state, and events tell your automations about it:

- `home_timer_finished` when the timer runs out,
- `home_timer_reminder` every few minutes while it rings (with the overtime),
- `home_timer_acknowledged` when it is acknowledged – by the *Acknowledge all* button, the action, or by itself after a while (`by: timeout`).

What happens then is up to you: a phone notification, an announcement, a light. See [Automations](automations.md).

## Options

Per area: Settings → Devices & services → Home Timer → the area's entry → **Configure**. Saving reloads the area; running timers go on.

| Option | Default | Description |
|---|---|---|
| Ring until acknowledged | on | the timer stays *ringing* after it runs out. Off: it is gone right after `home_timer_finished`; timers that ring when you turn it off are acknowledged (`by: timeout`). |
| Reminder every | 2 min | while a timer rings, `home_timer_reminder` every so many minutes. 0: no reminders. |
| Stop ringing after | 30 min | a ringing timer is acknowledged by itself after so many minutes (`by: timeout`). 0: it rings until acknowledged. |
| Report missed timers | on | a timer that ran out while Home Assistant was restarting fires `home_timer_finished` with `late: true` right after the start. Off: it is dropped. |

## Restarts

Timers are stored with their end time, so a restart of Home Assistant loses nothing:

- an active timer goes on counting; if it ran out during the restart, see *Report missed timers*. Its overtime counts from when it ran out, but reminders and auto-dismiss count from the start, so you get the full time to react,
- a ringing timer keeps ringing; reminders and auto-dismiss continue from when it started ringing (if the auto-dismiss time has passed, it is acknowledged with `by: timeout`),
- a paused timer stays paused with its remaining time.

Missed timers are reported once Home Assistant has started, so automations triggered by the events see them.
