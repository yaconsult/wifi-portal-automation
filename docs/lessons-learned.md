# Lessons Learned — Field Notes on Tasker + Termux Automation

Distilled operational findings from months of running this system on
a rooted phone. Most of these cost real debugging time; several
contradict Tasker's documented or intuitive behavior. They are the
reason the generated project looks the way it does.

## Tasker project imports

**Imports merge — they never replace.** Importing XML over an
existing project keeps the old task definitions and only adds new
elements. Observed symptom: a scheduled task kept invoking a renamed
wrapper (the new definition never took effect) while a newly added
profile appeared alongside but never fired.

**Deleting a project tab does not delete its tasks.** Task names are
global across all projects. Leftover tasks either block a re-import
("name already exists") or merge against it. The only reliable
import procedure:

1. Delete the old project tab
2. Delete leftover tasks/profiles in the global TASKS/PROFILES tabs
3. Import the new XML
4. **Exit Tasker gracefully** (Back/Exit — see next item)
5. Relaunch Tasker and verify (the example project's `MVW_ShowVersion`
   task flashes the embedded generation stamp)

**Tasker persists state only on graceful exit.** Edits and imports
live in memory until a clean exit writes them to disk. A force-stop
or swipe-away *silently discards an entire import* — observed:
a version-check task flashed the new generation while scheduled
ticks kept running the old persisted definitions, and after a
force-stop the GUI showed the old project again. Manual runs show
in-memory state; only scheduled-run markers reveal persisted state.

**Name-prefix your tasks and profiles.** Because names are global,
an import or "orphan cleanup" in an unrelated project can delete or
collide with yours. Prefixing every task (`MVW_NudgeWifi`) and
profile (`MVW cmvwifi Auto Connect`) made collisions effectively
impossible and made our tasks unmistakable during any cleanup.

## Tasker action gotchas

**Run Shell fails on any nonzero exit.** Tasker flags the action as
an error and aborts the task if the shell command's exit status is
nonzero — even when the command "worked." A check ending in a bare
`[ test ]` or `&&` chain returns 1 on the false path; wrap it as
`if [ test ]; then echo MARKER; fi` (always exit 0) and test the
captured output variable in an If action instead. The error shows up
in `/sdcard/Tasker/log/runlog.txt` as `A Err` / `T ExitErr` and as a
"Tasker Action Error" notification.

**WiFi Near is unreliable in the background.** Android's background
scan throttling (~4 scans/2h unrooted, still throttled when rooted
below the surface) makes WiFi Near results stale — observed 15–30
minute delays between a network being visible and the profile
firing. Periodic `Time` profiles plus `cmd wifi` calls under root
replaced it entirely.

**WiFi Connected has a fire latency of its own** — ~minutes from
association to task start under doze. Fine for portal handling
(which only needs the association), wrong for time-critical joins.

**Event profiles fire once per event.** `Display Unlocked`
(event code 1000) is the right trigger for "user just picked up
the phone" checks — but throttle the work behind it, since unlock
bursts fire repeatedly (see `--min-interval` on `wpa-nudge`).

**Continue-on-error serializes as `<se>false</se>`, and Tasker
drops unknown elements silently.** The "Continue Task After Error"
flag lives at the action level as `<se>false</se>` (stop-on-error =
false), confirmed from Tasker-authored exports. A plausible-but-
wrong element (`<con>true</con>`) imports without complaint and is
simply absent from Tasker's persisted state — the project gen stamp
can't catch that. Verify serialization changes by grepping
`/sdcard/Tasker/autobackup.xml` for the *element*, not the version
stamp.

## Termux / Termux:Tasker on modern Android

**Phantom process killer kills Termux children.** Android 12+
`settings_enable_monitor_phantom_procs` kills a process's children
past a limit — disable it (root: `settings put global
settings_enable_monitor_phantom_procs false`; needs redo after some
reboots — the wrappers re-apply it on every run).

**Battery optimization and wake locks matter.** Whitelist Termux
(`dumpsys deviceidle whitelist +com.termux`) and wrap wrapper runs in
`termux-wake-lock`/`termux-wake-unlock` or long portal flows get
frozen mid-POST.

**`allow-external-apps = true`** must be set in
`~/.termux/termux.properties` or the Tasker plugin can't execute
wrappers at all — failure is silent (plugin reports timeout).

**Termux files are private** — Tasker (or anything else) can't read
Termux's home. Logs meant for Tasker-side checks go under `/sdcard`;
wrappers live at `~/.termux/tasker/` and are delivered via a
`/sdcard` staging push + `su -c cp` (see `docs/android-deployment.md`).

**Magisk toasts every `su` grant.** With a task invoking `su` on a
15-minute timer, the default per-app "granted superuser rights" toast
becomes spam. Toggle the notification (bell) icon off per app in
Magisk's Superuser tab (Termux, Tasker, Shell) — grants stay logged,
only the popup stops. Current Magisk has no global toggle; the flag
is the `notification` column in `magisk.db`'s `policies` table.

**A dead Termux fails silently — the plugin still reports OK.**
Termux:Tasker acknowledges the intent delivery, not wrapper
execution; while Termux's process is dead, tasks log `A OK` and
nothing runs (observed ~20h of silent no-ops while every other
signal looked healthy). Defenses: (1) a root Run Shell before each
plugin call that resurrects Termux (`pidof com.termux || am start -n
com.termux/.app.TermuxActivity`) — a dead Termux can't run a wrapper
to heal itself, so the check must come from outside; (2) a watchdog
that stats the *wrapper's* log, not the Tasker-side marker log —
Tasker markers keep their own log fresh while Termux writes nothing.

**"It was working before" is a weak signal.** Two field failures
traced to latent bugs, not regressions: a stop-on-error connect
action that only aborted once a newer proactive-nudge feature made
"already associated when the event task fires" common, and the
silent-Termux mode that only needed Termux to actually die once.
Environmental factors (a Pixel OTA that reset
`settings_enable_monitor_phantom_procs` and broke su until Magisk
re-patched) extended the outage but didn't cause it — check whether
the failing path was ever *exercised* before, not just present. And
attribute honestly: every defect in that outage was in our generated
project and monitoring design, not the platform — including one in
the fix itself (an invented `<con>` element Tasker silently dropped;
the real flag is `<se>false</se>`).

## WiFi-specific findings

**`cmd wifi connect-network <ssid> open`** (root) is the reliable
join primitive — no stored passphrase needed for open networks, no
UI automation. `-d` marks the added network autojoin-disabled: used
to keep the fallback tier from ever stealing a preferred slot.

**Android's selector actively deprioritizes portal networks** —
"no internet" verdicts and past failures push them down the scan
order. Without the nudge, joining a visible-but-burnt network took
up to ~30 min of organic retry; with it, seconds.

**The WiFi interface name isn't stable** — observed `wlan0` *and*
`wlan1` across different days on the same phone. Always auto-detect
(`ip -o addr` over the usual names, or check which has a private
IPv4) rather than hardcoding `wlan0`.

**`cmd wifi status` is the cheap check** — ~100ms, no radio work.
The nudge runs it first and exits immediately when already
connected; scans only when disconnected or sitting on the fallback.

## Portal handling

**Never assume the portal host is the gateway.** Portal
infrastructure routinely lives elsewhere — the Costco example's
portal is a vendor CDN (`portal.gc1.mist.com`) reached via a 302 off
the probe URL. Follow the redirect chain and derive the host
dynamically (`captive_portal.detect_portal`).

**A 200 from the accept POST is not internet.** Several portals
return the "accepted" page before the MAC is actually authorized.
Always re-probe the canary URL and expect clean content.

**On Android, bind the HTTP session to WiFi.** With cellular data
enabled, Android's policy routing sends new connections over
cellular — portal requests to WiFi-only IPs silently fail.
`SO_BINDTODEVICE` in `wifi_binding.py` is the fix; it's the single
most important line in the Android path.

**MAC-remembered portals beat automation.** Some venues authorize
the device MAC for months — accept once manually and the "portal"
never appears again, at any location sharing the backend. Always
capture first (`wpa-portal-probe`) before building a handler; you
may find there's nothing to build.
