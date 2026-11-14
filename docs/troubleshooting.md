# WiFi Portal Automation - Troubleshooting Guide

## Symptom Index

**Linux / laptop:**
- [Service Won't Start](#service-wont-start)
- [D-Bus Permission Errors](#d-bus-permission-errors)
- [Not Connecting to cmvwifi](#not-connecting-to-cmvwifi)
- [Interferes with dd-wrt Connection](#interferes-with-dd-wrt-connection)
- [Captive Portal Not Accepted](#captive-portal-not-accepted)
- [Service Stops After Suspend/Resume](#service-stops-after-suspendresume)

**Android / Termux:**
- ["MVW cmvwifi Auto Connect" profile doesn't fire](#mvw-cwpa-auto-connect-profile-doesnt-fire)
- [Profiles enabled but never fire (after re-import)](#profiles-enabled-but-never-fire-after-re-import)
- [Scheduled tasks run old behavior after re-import](#scheduled-tasks-run-old-behavior-after-re-import)
- [Phone takes 15-30 minutes to join cmvwifi](#phone-takes-15-30-minutes-to-join-cmvwifi)
- [Connected to cmvwifi but no internet for a long time](#connected-to-cmvwifi-but-no-internet-for-a-long-time)
- [Termux plugin times out (error code 2)](#termux-plugin-times-out-plugin-did-not-respond-before-timing-out-error-code-2)
- [Plugin reports OK but wrappers never run](#plugin-reports-ok-but-termux-wrappers-never-execute-dead-termux)
- [Delay between "Portal handling complete" and working internet](#delay-between-portal-handling-complete-and-working-internet)
- [HTTP requests timing out with cellular ON](#http-requests-timing-out-with-cellular-on)
- ["Could not determine IPv4 address for interface 'wlan0'"](#could-not-determine-ipv4-address-for-interface-wlan0)
- [`ip addr` returns "cannot bind netlink socket"](#ip-addr-returns-cannot-bind-netlink-socket-permission-denied)
- [Connected to cmvwifi but cellular stays default](#connected-to-cmvwifi-but-cellular-stays-default-no-wifi-icon)
- [Frequent "granted superuser" toasts](#frequent-granted-superuser-rights-toasts-every-15-min)
- [Debugging on the phone](#debugging-on-the-phone)

**General:** [Debug Mode](#debug-mode) · [Log Analysis](#log-analysis) · [Reporting Issues](#reporting-issues)

## Common Issues

### Service Won't Start

**Problem**: `systemctl --user start wpa-auto` fails

**Check**:
```bash
# Check service status
systemctl --user status wpa-auto

# Check for errors in logs
journalctl --user -u wpa-auto --since "5 minutes ago"
```

**Common Causes**:

1. **Missing dependencies**
   ```bash
   # Check if uv is installed
   which uv
   
   # Check if python3-dbus is installed
   python3 -c "import dbus; print('OK')"
   # If fails: sudo dnf install python3-dbus
   
   # Check if requests is available
   python3 -c "import requests; print('OK')"
   ```

2. **Virtual environment issues**
   ```bash
   # Reinstall from scratch
   cd ~/wifi-portal-automation
   rm -rf .venv
   uv venv --system-site-packages
   uv sync
   ```

3. **Wrong ExecStart path**
   ```bash
   # Check the wrapper script exists
   cat ~/.local/bin/wpa-auto
   
   # Update service file if needed
   systemctl --user daemon-reload
   ```

### D-Bus Permission Errors

**Problem**: `AccessDenied: Sender is not authorized` when running as systemd user service

**Cause**: Systemd user service doesn't have D-Bus permissions to access NetworkManager

**Solution**:

**Quick workaround** (for testing):
```bash
# Stop systemd service
systemctl --user stop wpa-auto

# Run in user session instead (avoids permission issues)
wpa-auto --daemon &
```

**Proper fix** (recommended):

Create a polkit rule to allow your user to access NetworkManager:

```bash
sudo tee /etc/polkit-1/rules.d/50-wpa-auto.rules << 'EOF'
/* Allow wpa-auto user service to access NetworkManager */
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 &&
        subject.user == "USERNAME") {
        return polkit.Result.YES;
    }
});
EOF
# Replace USERNAME with your actual username
```

Then reload and restart:
```bash
sudo systemctl restart polkit
systemctl --user restart wpa-auto
```

**Verify the fix:**
```bash
wpa-auto --once --verbose
# Should show: "Current state: ssid='cmvwifi', internet=True"
```

### Not Connecting to cmvwifi

**Problem**: Service running but not connecting when cmvwifi is available

**Diagnosis Steps**:

1. **Check if cmvwifi is visible**:
   ```bash
   nmcli device wifi list | grep cmvwifi
   ```

2. **Run manual test with verbose output**:
   ```bash
   cd ~/wifi-portal-automation
   uv run wpa-auto --once --verbose
   ```

3. **Check NetworkManager can connect**:
   ```bash
   # Try manual connection
   nmcli device wifi connect cmvwifi
   
   # If successful, captive portal should appear in browser
   ```

4. **Test captive portal detection**:
   ```bash
   curl -v http://detectportal.firefox.com/canonical.html
   # Should redirect if behind portal
   ```

**Common Causes**:

- **cmvwifi not in range** - Check signal strength
- **Portal detection failing** - May need to adjust detection logic
- **Connection timeout** - Increase timeout in code

### Interferes with dd-wrt Connection

**Problem**: Service disrupts existing dd-wrt connection

**Expected Behavior**: Service should NOT interfere with dd-wrt

**Verification**:
```bash
# When on dd-wrt, service should log:
# "Current state: ssid=dd-wrt, internet=True"
# And take no action

journalctl --user -u wpa-auto -f
```

**If interference occurs**:
1. Check decision logic in logs
2. Verify dd-wrt is in preferred networks list
3. Check if internet detection is working

### Captive Portal Not Accepted

**Problem**: Connected to cmvwifi but no internet (portal not accepted)

**Diagnosis**:

1. **Check if connected to cmvwifi**:
   ```bash
   nmcli connection show --active | grep cmvwifi
   ```

2. **Get portal host**:
   ```bash
   # The portal host is extracted from the redirect URL, not the gateway
   curl -v --max-redirs 5 http://1.1.1.1/ 2>&1 | grep "Location:"
   # Or follow redirects and check the final URL:
   curl -L -o /dev/null -w "%{url_effective}\n" http://1.1.1.1/
   ```

3. **Test portal acceptance manually**:
   ```bash
   # Extract the host from the redirect URL (e.g. 10.64.2.21:9997)
   PORTAL_HOST="10.64.2.21:9997"
   curl -X POST "http://${PORTAL_HOST}/forms/guest_toued" \
     -d "origurl=http://www.google.com" \
     -d "ok=Accept and Continue" \
     -v
   ```

4. **Check portal detection**:
   ```bash
   curl -I http://detectportal.firefox.com/canonical.html
   ```

**If manual works but auto doesn't**:
- Check logs for portal handling errors
- May need to increase delays/timeouts
- Gateway detection might be failing

### Service Stops After Suspend/Resume

**Problem**: Service doesn't resume after laptop wakes from sleep

**Check systemd configuration**:
```bash
# Check if service is still enabled
systemctl --user is-enabled wpa-auto

# Check last start time
systemctl --user status wpa-auto | grep "Active:"
```

**Solution**:
The service has `Restart=always` which should handle this. If not:

1. Check systemd user instance is running:
   ```bash
   systemctl --user status
   ```

2. Consider adding to systemd user linger:
   ```bash
   # Enable lingering (allows user services without login)
   sudo loginctl enable-linger $USER
   ```

## Debug Mode

### Enable Verbose Logging

Edit the service to add `--verbose`:
```bash
# Edit service file
systemctl --user edit wpa-auto

# Add to [Service] section:
# ExecStart=
# ExecStart=%h/.local/bin/wpa-auto --daemon --interval 60 --verbose
```

Or run manually:
```bash
cd ~/wifi-portal-automation
uv run wpa-auto --daemon --interval 30 --verbose
```

### Run Tests

```bash
cd ~/wifi-portal-automation

# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=wifi_portal_automation --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_controller.py -v
```

### Manual Component Testing

**Test NetworkManager interface**:
```bash
python3 << 'EOF'
import sys
sys.path.insert(0, 'src')
from wifi_portal_automation.network_manager import get_connection_info, find_network

# Check current connection
info = get_connection_info()
print(f"Current: {info}")

# Scan for cmvwifi
result = find_network("cmvwifi")
print(f"cmvwifi found: {result}")
EOF
```

**Test captive portal**:
```bash
python3 << 'EOF'
import sys
sys.path.insert(0, 'src')
from wifi_portal_automation.captive_portal import (
    detect_captive_portal, 
    get_default_gateway,
    verify_internet_connectivity
)

# Detect portal
is_captive, redirect = detect_captive_portal()
print(f"Captive: {is_captive}, Redirect: {redirect}")

# Get gateway
gateway = get_default_gateway()
print(f"Gateway: {gateway}")

# Check internet
has_internet = verify_internet_connectivity()
print(f"Internet: {has_internet}")
EOF
```

## Log Analysis

### Common Log Messages

| Message | Meaning | Action |
|---------|---------|--------|
| `Current state: ssid=dd-wrt, internet=True` | On home WiFi | Normal, no action |
| `Decision: none - Already connected to preferred` | Correctly idle | None needed |
| `Scanning for WiFi networks...` | Starting scan | Normal operation |
| `Decision: connect_cmvwifi` | Will connect to public | Wait for connection |
| `Connecting to cmvwifi...` | Starting connection | Should complete soon |
| `Captive portal accept: 200` | Portal accepted | Should have internet |
| `Successfully connected with internet access` | Success | None needed |
| `NetworkManager error: ...` | D-Bus/scan error | Check NetworkManager |
| `Scan failed: AccessDenied` | Permission issue | Check user permissions |

### Getting More Logs

```bash
# Follow logs in real-time
journalctl --user -u wpa-auto -f

# Get last 100 lines
journalctl --user -u wpa-auto -n 100

# Get logs since last boot
journalctl --user -u wpa-auto --since "today"

# Get logs with specific time range
journalctl --user -u wpa-auto --since "2026-05-07 10:00" --until "2026-05-07 11:00"
```

## Reporting Issues

When reporting issues, include:

1. **Service status**:
   ```bash
   systemctl --user status wpa-auto
   ```

2. **Recent logs**:
   ```bash
   journalctl --user -u wpa-auto --since "1 hour ago"
   ```

3. **Network status**:
   ```bash
   nmcli device status
   nmcli connection show --active
   ```

4. **Test output**:
   ```bash
   cd ~/wifi-portal-automation
   uv run wpa-auto --once --verbose 2>&1
   ```

5. **Environment**:
   - Fedora version
   - NetworkManager version (`nmcli --version`)
   - Python version (`python3 --version`)

## Android/Termux Issues

### "MVW cmvwifi Auto Connect" profile doesn't fire

**Problem**: The profile is enabled (green dot) but never activates
when the phone is on cmvwifi.

**Cause**: Wrong or stale import. The current profile uses **WiFi
Connected** (fires instantly when Android associates to cmvwifi).
An older import may still be a WiFi Near profile, which polls scan
results — throttled by Android to roughly once per 30 minutes for
background apps — or may have broken args from an old generator bug
(an Int sent where a Str was expected became `MAC="0"`).

**Fix**: Delete the `MVwifiAuto-Termux` project in Tasker and
re-import the current XML. In the imported profile, verify it says
**WiFi Connected** with SSID `cmvwifi` — not WiFi Near.

```bash
cd ~/wifi-portal-automation
uv run python -m wifi_portal_automation.tasker_gen --termux -o android/MVwifiAuto-Termux.prj.xml
adb push android/MVwifiAuto-Termux.prj.xml /sdcard/Tasker/projects/
```

**Verify**: Toggle WiFi off/on while connected via adb, or check
`~/storage/shared/Tasker/mvwifi_history.log` — every trigger appends
a `tasker | MVW_PortalOnConnect fired` marker.

### Profiles enabled but never fire (after re-import)

**Problem**: All profiles show as enabled in Tasker, but nothing
fires — no `tasker` markers in `mvwifi_history.log`, and a manual
task run works fine.

**Cause**: Tasker quirk — after importing a project, the running
monitor does not register the new profile contexts until the Tasker
app is opened once. Observed as 3 days of silence across both a WiFi
Connected profile and a Time profile after a delete+reimport;
everything started the moment Tasker was opened.

**Fix**: Open Tasker after every import. If it still doesn't fire,
toggle the profile off/on, and check that Tasker has **Alarms &
reminders** permission (needed for Time contexts).

### All markers silent — project deleted entirely

**Problem**: `mvwifi_history.log` stops updating completely — no
`tasker` markers, no `termux` markers, for many hours — while
Tasker itself is running fine.

**Cause**: The `MVwifiAuto-Termux` project was deleted from
Tasker — e.g. during cleanup for an unrelated project's import
(observed: a legacy `MVwifiAuto.prj.xml` was imported by mistake
and the Termux project got swept out; ~12h of silence). Unlike
the "stale merge" case, nothing fires at all because the project
is absent from Tasker's saved state.

**Diagnose** (adb + root):
`grep -oE "<name>[^<]*" /data/data/net.dinglisch.android.taskerm/files/autobackup.xml`
— if `MVwifiAuto-Termux` isn't in the project list, it was deleted.

**Fix**: Re-import `MVwifiAuto-Termux.prj.xml` (full procedure in
`docs/android-termux-setup.md`). The separate **MVwifiWatchdog**
project (one-time import) catches exactly this: hourly it stats
the history and nudge logs and posts a notification when Tasker
markers go quiet for >24h (`tasker-silent`), when Tasker is fresh
but the Termux side is quiet >1h (`termux-silent`), or when recent
nudge results show a `no_root` streak (`no_root-streak`). Findings
land in `/sdcard/Tasker/mvwifi_watchdog.log` — a separate file so
the watchdog never refreshes the mtime it monitors.

### "Tasker Action Error" notification — watchdog `A Err` in runlog

**Problem**: Periodic "Tasker Action Error" notifications; Tasker's
run log (`/sdcard/Tasker/log/runlog.txt`) shows
`A Err ID... MVW_HistoryWatchdog.<action>` and `T ExitErr`, even
though the history log is fresh and everything is actually working.

**Cause**: Tasker marks a **Run Shell** action as failed whenever
the command exits nonzero. The watchdog's age check originally
ended with `[ $AGE -gt 86400 ] && echo STALE` — when the log is
*fresh* the test returns 1, `&&` short-circuits, and the shell
exits 1 → action error every 6h (the healthy case, ironically).
Any Run Shell whose last command is a bare test or `&&`-chain
hits this.

**Fix**: The shell must always exit 0 and signal state via stdout
instead — the watchdog now ends
`if [ $AGE -gt 86400 ]; then echo STALE; fi` and the `If` action
tests `%output` for `STALE`. Reimport `MVwifiWatchdog.prj.xml`
(one-time; it's a separate project tab).

### Connected to cmvwifi but cellular stays default (no WiFi icon)

**Problem**: Phone associates to cmvwifi but the status bar shows no
WiFi icon — apps keep using cellular. `dumpsys connectivity` shows
`CAPTIVE_PORTAL` on the WiFi network and `Active default network` is
the mobile interface.

**Cause**: the captive portal was never (re-)accepted. On older
builds, `MVW_ConnectAndRun` aborted before the portal handler: the
periodic nudge can associate first; by the time the delayed
WiFi-Connected event task ran, `%WIFII` read `>>> SCAN <<<` so the
already-connected skip-guards missed (they could never match — the
`~` op needs a whole-string match on a multi-line `%WIFII`), the
redundant "Connect To WiFi" action errored, and the task exited
before the portal script. Runlog showed `A Err … Connect To WiFi` +
`ExitErr`.

**Diagnose**:
```bash
adb shell 'su -c "dumpsys connectivity"' | grep -A2 "ni{WIFI"   # CAPTIVE_PORTAL?
grep "PortalOnConnect\|ConnectAndRun" /sdcard/Tasker/log/runlog.txt | tail
tail /sdcard/mvwifi_nudge.log                                    # portal_rehandled?
```

**Immediate fix**: run the portal handler manually —
`adb shell 'su -c "/data/data/com.termux/files/home/.termux/tasker/mvwifi_portal"'`
— then Android re-validates and promotes WiFi within seconds.

**Permanent fix** (gen `434d1e`+): two layers. The event task is now
`MVW_PortalOnConnect` — no connect step at all (association is
guaranteed by the WiFi Connected profile), so nothing can abort
before the portal handler. And every preferred-nudge tick passes
`--recheck-portal`: when connected to a `portal_ssids` network the
nudge parses the WiFi agent's `VALIDATED`/`CAPTIVE_PORTAL`
capabilities and re-runs the handler itself — a stuck captive state
self-repairs within ~15 min. `portal_ssids` is set in
`networks.toml` (default: the municipal pair).
Reimport the project (full cycle in `docs/android-termux-setup.md`).

### Scheduled tasks run old behavior after re-import

**Problem**: After re-importing updated XML, scheduled runs keep
doing the *old* thing — e.g. markers fire but the plugin errors,
markers lack the `[gen xxxxxx]` stamp, or `nudge via shim` lines
appear in `mvwifi_history.log`. Newly-added profiles may exist but
never fire.

**Cause**: Tasker project import **merges** rather than replaces —
existing task definitions are kept and only new elements are added.
Observed on-device: a scheduled `MVW_NudgeWifi` kept invoking the renamed
`cmvwifi_nudge` wrapper (logcat: `TermuxTasker.FireReceiver`
"executable not found") while the new xfinity profile appeared
alongside it but never fired.

**Diagnose**: `tail ~/storage/shared/Tasker/mvwifi_history.log` —
the `[gen xxxxxx]` in each marker must match the gen id in the XML
at `/sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml` (`grep -o
"gen [0-9a-f]*" .../MVwifiAuto-Termux.prj.xml`),
and no `via shim` line should appear. Or run the `MVW_ShowVersion` task
in Tasker — it flashes the imported generation. **Caveat**: a manual
task run (or `MVW_ShowVersion`) shows the *in-memory* definition — the
scheduled ticks can still run older persisted defs. The marker gen
on a scheduled tick is the authoritative check.

**Also rule out a stale file**: if the persisted gen never changes no
matter how carefully the import cycle is done, you probably imported a
*stale copy of the XML itself* — check for duplicates before importing:
`find /sdcard -name 'MVwifiAuto-Termux.prj.xml'` must return exactly
`/sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml`. Copies at
`/sdcard/` or `/sdcard/Tasker/` root are leftovers (manual pushes,
old deploys) — the import dialog happily opens them.
`deploy_android.sh` pushes only to `Tasker/projects/` and now removes
strays; manual `adb push` should use the same path.

**Cause — three separate persistence traps:**

1. **Merge import**: importing over an existing project keeps old
   task definitions and only adds new elements.
2. **Orphaned tasks**: deleting the project tab leaves its tasks in
   the global list; a later import fails with "the name X already
   exists" — or worse, merges against the orphans.
3. **Unsaved state**: Tasker holds edits and imports in memory and
   only writes them to disk on a graceful exit. A force-stop or
   swipe-kill discards an import entirely — observed on-device:
   `MVW_ShowVersion` flashed the new gen while scheduled ticks kept
   firing the persisted old defs, and after a force-stop the GUI
   showed the old version again.

**Fix**: Delete the project tab *and* its leftover tasks/profiles
(TASKS/PROFILES tabs: `MVW_PortalOnConnect`, `MVW_RunPortalScript`,
`MVW_CostcoConnect`, `MVW_NudgeWifi`, `MVW_NudgeXfinity`,
`MVW_ShowVersion`, any wifi/nudge profiles — **plus the old
`MVW_ConnectAndRun`**, which was renamed and would otherwise linger
as an orphan), then Import Project, then **exit Tasker
gracefully** (Back or its Exit menu — this is what persists the
import), then relaunch Tasker so contexts register against the new
definitions. Verify the marker gen ids match the XML on the next
scheduled tick.

### Connected to cmvwifi but no internet for a long time

**Problem**: The phone shows it is connected to cmvwifi, but apps
report no internet for many minutes after arriving.

**Cause**: The captive portal wasn't accepted yet — the trigger
ran late. With an old WiFi Near profile, Android's scan throttling
can delay Tasker ~30 min after the actual association. With WiFi
Connected this can't happen; it fires at association.

**Diagnose**: Check `~/storage/shared/Tasker/mvwifi_history.log`
and compare the `tasker | MVW_PortalOnConnect fired` timestamp with when
the phone actually connected (Settings → WiFi, or logcat). A large
gap means a stale WiFi Near import.

**Fix**: Same as above — re-import the current XML so the profile
is WiFi Connected.

### Phone takes 15-30 minutes to join cmvwifi

**Problem**: The phone sits in range of cmvwifi but doesn't
associate for a long time, even though auto-connect is on. Once it
does join, portal handling completes in seconds.

**Cause**: Android's network selector, not Tasker/Termux. Every
cmvwifi join initially fails Android's captive-portal validation
("no internet"), and historical DHCP/association failures further
poison its score — check `su -c 'cmd wifi list-networks'` or
`dumpsys wifi` for `CMD_UNWANTED_NETWORK`,
`numConsecutiveConnectionFailure`, and BSSID blocklist entries.
Screen-off PNO scans are throttled on top of that.

**Fix**: The `MVW cmvwifi Periodic Nudge` Time profile (every 15 min,
requires root) runs `~/.termux/tasker/wifi_nudge`, which issues
`cmd wifi connect-network cmvwifi open` whenever cmvwifi is in scan
results but unassociated. Verify it exists and fires:

```bash
# On the phone (Termux) — history shows 'nudge' lifecycle lines:
tail ~/storage/shared/Tasker/mvwifi_history.log
# Detail log:
cat ~/storage/shared/mvwifi_nudge.log
# Manual test:
su -c 'cmd wifi connect-network cmvwifi open'
```

**Nudge action values** (in `mvwifi_nudge.log` "nudge result"
lines): `already_connected` / `connected_elsewhere` / `wifi_disabled`
/ `target_absent` are no-ops; `deferred` means the xfinitywifi run
saw a preferred SSID in scan results and yielded to it;
`connect_requested` joined a target; `promoted` moved the phone off
a fallback SSID (xfinitywifi) onto a visible preferred one;
`fallback_stay` means it's on xfinitywifi and
no preferred network was in range. `xfinitywifi` is a **fallback
tier**: joined only while fully disconnected, saved with auto-join
disabled (`-d`), and managed by the separate `xfinitywifi Periodic
Nudge` profile — disable that profile in Tasker to exclude
xfinitywifi entirely.

**Phone stays on xfinitywifi even though a better network is in
range**: promotion off a fallback works two ways — the cmvwifi nudge
explicitly promotes onto cmvwifi when it appears, while saved home /
Costco networks are handled by Android's own selector (autojoin on +
higher score wins). If neither happens within ~15 min: confirm
`MVW cmvwifi Periodic Nudge` is enabled and firing (history log), and
check the saved configs — `su -c 'dumpsys wifi'` should show
`allowAutojoin=false` for xfinitywifi and `true` for the preferred
ones. The nudge can only promote onto *open* networks — promotion
onto dd-wrt/Costco is Android's job (it needs no passphrase).

If the profile is missing, re-import `MVwifiAuto-Termux.prj.xml`.
On an unrooted phone the nudge exits with "no usable root shell";
manual joins or screen-on retries are the fallback.

### Termux plugin times out: "plugin did not respond before timing out, error code 2"

**Problem**: Tasker reports `termux step 1, task: runportalscript
plugin did not respond before timing out. error code 2`, even with
the action timeout raised to 30 seconds.

**Cause**: This is usually **not** a slow script — it means
Termux:Tasker never reported the command's completion back to
Tasker. The most common reason is Android killing the Termux
process mid-execution:

- **Termux is not battery-exempt** — Tasker is typically
  whitelisted but Termux is not, so Android can kill Termux
  while a plugin command is running
- **Phantom process killer enabled** (Android 12+ default) —
  Android kills "phantom" child processes spawned by apps in
  the background. Termux's child processes (bash, python) are
  tracked as phantom processes
- A malformed `service_execute` intent can also crash
  `TermuxService` outright (NullPointerException in
  `TermuxShellUtils.setupProcessArgs`), producing the same
  "plugin did not respond" symptom

**How to tell** (check the log on the phone):

```bash
cat ~/storage/shared/mvwifi_tasker.log
pgrep -af mvwifi    # is the script still alive?
```

The log is overwritten each run and ends with an outcome line
("Run completed successfully" / "Run FAILED"). If the log ends
abruptly mid-request (e.g. urllib3 "Starting new HTTP connection"
lines with no responses) **with no outcome line** and the process
is gone, the process was killed — not just slow. The script's own
HTTP timeouts cap its runtime at roughly 2 minutes worst case, so
a 30s+ plugin timeout with a dead process means the plugin result
was lost.

**Fix**: re-run `./scripts/deploy_android.sh` — it applies all of
the protections below automatically. Or apply them manually:

```bash
adb shell dumpsys deviceidle whitelist +com.termux
adb shell settings put global settings_enable_monitor_phantom_procs false
adb shell pm grant com.termux android.permission.WRITE_SECURE_SETTINGS
```

The last grant lets the wrapper script re-disable the phantom
killer itself on every run (self-heal after OS updates). The
wrapper also holds `termux-wake-lock` during the run.

On rooted phones, a root shell self-heal action runs before every
Termux plugin call (all nudge tasks, `MVW_PortalOnConnect`, and the
per-network portal tasks) that re-applies all three protections on
every trigger — so a regressed setting is repaired before it can
cause a failure — and resurrects Termux itself if its process is
dead. The action has continue-task-after-error set, so unrooted
phones skip it safely.

Both settings live in `/data` and survive full-image flashes
done without the `-w` wipe flag, but verify them after each
update — or run `./scripts/verify_android.sh` to check the whole
Android-side state at once:

```bash
adb shell dumpsys deviceidle whitelist | grep termux
adb shell settings get global settings_enable_monitor_phantom_procs   # want: false
```

Also exempt Termux via the UI as a belt-and-suspenders measure:
Settings → Apps → Termux → Battery → **Unrestricted**.

### Plugin reports OK but Termux wrappers never execute (dead Termux)

**Problem**: Tasker tasks run on schedule, the Termux:Tasker plugin
action logs `A OK`, Tasker keeps writing its `| tasker |` history
markers — but no `| termux |` markers or nudge-log lines appear for
hours. Portal never handled, nudges never run, and nothing looks
wrong in the runlog.

**Cause**: The Termux process itself is dead (killed by Android or
an OTA; observed post-Pixel-update ~2026-11-11). The plugin reports
OK because the *intent* was delivered — it does not verify that
Termux then executes anything. This is the silent sibling of the
error-code-2 timeout above: a dead Termux doesn't even time out.

**Diagnose**:

```bash
adb shell 'su -c "pidof com.termux"'   # empty = Termux dead
# Tasker markers fresh but nudge log stale:
adb shell 'stat -c "%y %n" /sdcard/Tasker/mvwifi_history.log /sdcard/mvwifi_nudge.log'
```

**Fix**: nothing to do on a current build — the root self-heal
action before every plugin call now resurrects Termux
(`pidof com.termux || am startservice -n com.termux/.app.TermuxService`
with an activity-launch fallback, gen `434d1e`+). To recover
immediately, launch Termux once or
`adb shell 'am startservice -n com.termux/.app.TermuxService'`.

**Why the watchdog didn't catch this**: the old watchdog only
checked the history log's mtime, which Tasker markers kept fresh.
It now also stats `mvwifi_nudge.log` and alerts within ~1–2h when
the Termux side goes quiet while Tasker stays active, logging
`| watchdog | stale: termux-silent` to
`/sdcard/Tasker/mvwifi_watchdog.log`. (An earlier version still
required >24h absolute silence, which could not detect a ~20h
outage like this one.)

### Delay between "Portal handling complete" and working internet

**Problem**: The "Portal handling complete" toast appears but the
phone doesn't show a working WiFi connection for several minutes.

**Cause**: This is normal. After our script accepts the portal
terms, Android runs its own connectivity validation
(`connectivitycheck.gstatic.com/generate_204`) before switching
the default route from cellular to WiFi. Android batches these
checks — 1-3 minutes is normal, especially when cellular data
is active and being preferred.

**Fix**: None needed — this is OS behavior. If faster switchover
is needed, disable cellular data during the run (Tasker Settings
→ Mobile Data toggle) or use `svc data disable` with root, but
both trade convenience for speed.

### HTTP requests timing out with cellular ON

**Problem**: `wpa-android --once --verbose` hangs for ~40 seconds
per request and never completes.

**Cause**: Source IP binding alone doesn't bypass Android's policy
routing. The kernel still routes packets over cellular even with the
correct wlan0 source IP.

**Fix**: The code uses `SO_BINDTODEVICE` (socket option 25) for
kernel-level interface binding. This should work from Termux without
root. If it doesn't, verify with:

```bash
python scripts/test_bindtodevice.py
```

If `SO_BINDTODEVICE` reports "PERMISSION DENIED", your device may need
root or `CAP_NET_RAW`.

### "Could not determine IPv4 address for interface 'wlan0'"

**Cause**: The WiFi interface may be `wlan1` on some Pixel devices, or
WiFi isn't connected yet (no IP assigned).

**Fix**: The script auto-detects the interface. If that fails, find it
manually:

```bash
python scripts/detect_interface.py
```

Note: `ip addr` does not work from Termux (Android blocks netlink
sockets). The ioctl-based detection in the script works fine.

### `ip addr` returns "cannot bind netlink socket permission denied"

**Cause**: Android blocks netlink sockets for non-system apps. This is
expected — Termux cannot use `ip addr`.

**Fix**: Use the ioctl-based detection instead. The diagnostic script
`scripts/detect_interface.py` uses `SIOCGIFADDR` ioctl which works from
Termux.

### Frequent "granted superuser rights" toasts (every ~15 min)

**Problem**: Toast notifications like "com.termux has been granted
superuser rights" popping up on the nudge's schedule (every ~15 min),
plus whenever Tasker runs a root shell or a desktop session calls
`adb shell su`.

**Cause**: Magisk posts a toast on every `su` grant for each app
whose policy row has `notification=1`. The nudge, watchdog, and any
rooted Run Shell action all trigger one. Confirmed in logcat — the
poster is `pkg=com.topjohnwu.magisk`.

**Fix**: Magisk app → **Superuser** tab → tap the app (**Termux**,
**Tasker**, **Shell**) → toggle the **notification (bell) icon** off.
Grants are still logged; only the popup stops. Current Magisk has no
global toggle — it's per-app.

**Identify the toasting uid from a desktop** (if unsure who's
requesting):

```bash
adb shell 'su -c "cat /data/adb/magisk.db"' > /tmp/magisk.db
sqlite3 /tmp/magisk.db "SELECT uid, notification FROM policies WHERE notification=1;"
adb shell "cmd package list packages --uid <uid>"   # map uid → package
```

### Debugging on the phone

Write logs to a file for transfer:

```bash
# To Termux home (no permissions needed)
wpa-android --once --verbose --log-file ~/mvwifi.log

# To shared storage (requires termux-setup-storage first)
wpa-android --once --verbose --log-file ~/storage/shared/mvwifi.log
```

The log file is **overwritten on each run** — it always shows the
most recent run. Its last line records the outcome ("Run completed
successfully" or "Run FAILED"); a log that ends mid-run with no
outcome line means the process was killed.

The Tasker wrapper script writes to
`~/storage/shared/mvwifi_tasker.log` by default.

Transfer via `adb pull`, Google Drive, or `cat` and copy from the
Termux screen.
