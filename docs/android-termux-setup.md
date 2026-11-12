# Termux Android Setup Guide for MVwifiAuto

The recommended approach for Android: run the same Python
portal-handling code as the laptop, with HTTP traffic bound to wlan0
to bypass Android's cellular-preferred policy routing.

## How It Works (Summary)

cmvwifi requires accepting terms on a captive portal page before
granting internet access. This system automates that process using
three layers:

**Layer 1 — Tasker (detection & connection)**
- WiFi Connected profile fires the moment the phone associates to cmvwifi
- `MVW_ConnectAndRun` task connects to cmvwifi via Tasker Settings, waits
  5 seconds for DHCP
- Then triggers `MVW_RunPortalScript` via the Termux:Tasker plugin

**Layer 2 — Termux:Tasker (bridge)**
- Wrapper script at `~/.termux/tasker/mvwifi_portal` runs
  `wpa-android --once`
- Must use the full path (`/data/data/com.termux/files/usr/bin/wpa-android`)
  because the plugin runs in a minimal environment without PATH
- Requires `allow-external-apps = true` in `~/.termux/termux.properties`
  and the `RUN_COMMAND` permission granted to Tasker

**Layer 3 — Python (portal handling)**
- Auto-detects the WiFi interface (wlan0 on this device)
- Creates an HTTP session with both source IP binding AND
  `SO_BINDTODEVICE` — the latter is critical because source IP alone
  doesn't bypass Android's policy routing
- GETs `detectportal.firefox.com/canonical.html` → gets 302 redirect
- Extracts the portal host from the redirect `Location` header
  (dynamic, not the gateway)
- POSTs to `http://<host>/forms/guest_toued` to accept terms
- Verifies internet via `detectportal.firefox.com/success.txt` → 200

**Layer 0 — Periodic auto-join nudge (cmvwifi)**

The layers above only run *after* Android associates. Android's
network selector deprioritizes cmvwifi because every join initially
fails captive-portal validation ("no internet"), and screen-off scan
throttling delays retries — observed on-device as a 15-30 minute gap
with "Auto-connect" enabled. A Tasker **Time** profile (`cmvwifi
Periodic Nudge`, every 15 min) fires `MVW_NudgeWifi`, which runs
`~/.termux/tasker/wifi_nudge --preferred --fallback xfinitywifi` →
`wifi_nudge.py`. `--preferred` expands the module's
`PREFERRED_OPEN_SSIDS` constant (cmvwifi, MVwifi, Costco Member
Wifi) as targets — the Costco SSID lives in code because Termux's
Arguments field can't safely carry a multi-word SSID:

1. If already on a preferred WiFi → exits (never disrupts a working
   link); on the `xfinitywifi` fallback → scans, and promotes onto
   any visible preferred SSID
2. `cmd wifi start-scan` + `list-scan-results` (root) — is cmvwifi,
   `MVwifi` (same municipal network, alternate SSID), or
   `Costco Member Wifi` visible?
3. If visible but not associated → `cmd wifi connect-network <ssid>
   open`, and the existing WiFi Connected profile handles the portal

A second Time profile (`MVW xfinitywifi Periodic Nudge`) → `MVW_NudgeXfinity`
runs the same wrapper with `--ssid xfinitywifi --autojoin-disabled
--defer-to-preferred`: it joins open `xfinitywifi` only when fully
disconnected *and* no preferred network is in scan results (the defer
check prevents the two nudge tasks racing connect requests in the
same tick), and marks the saved config `-d` so Android never
self-joins it. `--defer-to-preferred` expands the module's
`PREFERRED_SSIDS` constant (cmvwifi, MVwifi, Costco Member Wifi,
dd-wrt, dd-wrt_5G) — the Costco SSID lives in code because Termux's
Arguments field can't safely carry a multi-word SSID. **Disabling
this profile in the Tasker UI removes
xfinitywifi from circulation entirely** — cmvwifi nudging and
promotion-away-from-xfinity are unaffected.

A third trigger supplements the 15-minute polls: an **Event →
Display → Display Unlocked** profile (`MVW Display Unlocked Nudge`)
fires `MVW_UnlockNudge` → the same wrapper plus `--min-interval
300`. The moment you need connectivity is when you unlock the
phone, and that's exactly where the periodic poll leaves a gap
(arrive at 14:03, unlock at 14:04 — the next tick may be 11 min
out). The status check always runs (~100ms, exits if already
connected); the scan+join is throttled to at most once per 5
minutes via a timestamp file, so a burst of
unlocks can't thrash the radio. Only `--min-interval` callers share
the window — the periodic ticks always scan, so an unlock just
after a tick may do one redundant scan (harmless). All the same
semantics apply — never-steal, promotion off xfinity, deferral.

#### Network precedence

Networks are in three tiers, and each tier is governed by a different
mechanism:

| Network | Tier | Joins via | Promoted off xfinitywifi via |
|---|---|---|---|
| dd-wrt / dd-wrt_5G | home (saved, autojoin on) | Android selector | Android selector |
| cmvwifi / MVwifi | preferred (open, portal) | nudge `connect-network` | nudge promotion |
| Costco Member Wifi | preferred (open, portal) | nudge `connect-network` (`--preferred`) | nudge promotion |
| SHGuestNet | preferred (open, portal) | nudge `connect-network` (`--preferred`) | nudge promotion |
| xfinitywifi | **fallback** (open, `-d`) | nudge only, when disconnected AND no preferred visible | — |

Promotion semantics:

- **On a fallback + preferred visible** → `wifi_nudge` promotes:
  `connect-network <preferred>`. Only *open* preferred networks can
  be nudge-promoted — `connect-network` requires a passphrase for
  wpa2/owe, which is not stored.
- **Disconnected + preferred visible** → `MVW_NudgeXfinity` *defers*
  (`--defer-to-preferred`): it does not request xfinitywifi when any
  of `PREFERRED_SSIDS` — cmvwifi, MVwifi, Costco Member Wifi, dd-wrt,
  dd-wrt_5G — is in scan results, so the two nudge tasks can't race
  their connect requests in a shared tick.
- **On a fallback + a home SSID appears** → Android promotes natively;
  saved autojoin-enabled networks always outscore the `-d` fallback
  (secured preferred networks can't be nudge-promoted — `connect-network`
  would need a passphrase, which is never stored).
- **On any non-fallback connection** → never pulled off, even if a
  preferred network appears (the never-steal rule is symmetric).
- **Profile disabled while on xfinitywifi** → only *joining* is
  gated; the cmvwifi nudge still promotes away. "Off" never strands
  you on a fallback.

Root is required for the `cmd wifi` calls. The nudge is silent when
it has nothing to do (WiFi off, already connected, or cmvwifi out of
range); one scan + an occasional connect request is negligible
battery.

### Why It's Hard on Android
- **Android policy routing** prefers cellular data over WiFi, so HTTP
  requests go over cellular even when WiFi is associated — the portal
  never sees the request and never gets accepted
- **The portal host is dynamic** — it's not the default gateway
  (gateway was `10.65.8.1`, portal host was `10.64.2.24:9997`), so you
  can't hardcode it
- **Termux can't use `ip addr`** — Android blocks netlink sockets, so
  interface detection has to use ioctl or `/proc` fallbacks
- **Tasker's HTTP actions don't work** — same policy routing problem,
  plus Tasker can't bind sockets to a specific interface

### Key Findings
1. **`SO_BINDTODEVICE` works from Termux without root** — it's a
   kernel-level socket option that forces packets through wlan0
   regardless of policy routing
2. **Source IP binding alone is insufficient** — Android ignores it and
   routes over cellular; 40-second timeouts in the log confirmed this
3. **The portal host is dynamic** — must be extracted from the
   redirect, not hardcoded
4. **The Termux:Tasker plugin uses action code `1256900802`** with a
   specific Bundle format — not code 130, and the Bundle must be child
   elements, not escaped text
5. **The log file is overwritten every run** —
   `~/storage/shared/mvwifi_tasker.log` always shows the last run;
   its final line records the outcome

## Why Termux Over Pure Tasker?

The pure-Tasker implementation is fundamentally broken on modern
Android — not because of a bug in our XML, but because of how Android
routes traffic (see [android-devlog.md](android-devlog.md), Sessions
14–19, for the full investigation):

1. **Android policy routing prefers cellular.** Whenever mobile data
   is enabled, internet-bound traffic goes over cellular — no matter
   what an app intends. The cmvwifi captive portal is only reachable
   over WiFi, so Tasker's `HTTP Request` actions hung ~40s and never
   reached the portal.
2. **Tasker has no interface binding.** `HTTP Request` (code 339)
   cannot bind a socket to `wlan0` — there is no action option for it.
   Neither source-IP binding nor `ip route add` help: policy routing
   ignores the main routing table and source addresses entirely.
3. **The apparent "it worked once" was a race condition.** During
   initial association, cmvwifi briefly becomes the only default
   route before Android re-establishes cellular preference — requests
   fired in that window succeeded. Timing luck, not reliability.

**The fix requires kernel-level socket binding.** `SO_BINDTODEVICE`
forces packets onto `wlan0`, bypassing policy routing — what
`curl --interface` does internally. No Tasker action can set socket
options, but it works from Termux **without root** (Termux's shell
user has `CAP_NET_RAW`), via a custom `requests` adapter in
`wifi_binding.py`. That constraint is what dictates the Termux
architecture — plus Python gets the dynamic portal-host extraction
(following the redirect to whichever `10.64.x.x:9997` host serves
that day) for free, which was clumsy as static Tasker actions anyway.

## Architecture

```
Tasker (WiFi Connected profile)
    │
    ▼
Run Shell (non-root): wpa-android --once
    │
    ▼
Termux Python environment
    │
    ▼
wifi_portal_automation.android.run_once()
    │
    ├─ create_wifi_session("wlan0")  # or auto-detect
    │    └─ InterfaceBoundAdapter: source_address + SO_BINDTODEVICE
    │       (kernel-level interface binding, bypasses policy routing)
    │
    └─ handle_cmvwifi_connection(session=...)
         ├─ detect_captive_portal(session=...)
         ├─ extract_portal_host(redirect_url)
         ├─ accept_cmvwifi_terms(portal_host, session=...)
         └─ verify_internet_connectivity(session=...)
```

## Prerequisites

1. **Rooted Google Pixel** with Magisk (for Tasker WiFi connection)
2. **Termux** installed (from F-Droid or GitHub releases, not Play Store)
3. **Termux:Tasker** installed (from F-Droid — optional but recommended for Tasker integration)
4. **Python 3.11+** in Termux
5. **Tasker** with **Tasker Settings** helper app (for WiFi connection)
6. `cmvwifi` network saved in Android WiFi settings with **auto-connect
   enabled** — Android does the association itself; the periodic nudge
   (below) compensates when its network selector backs off

> **Tip — quiet the su toasts:** Magisk pops a "granted superuser
> rights" toast on every `su` call — with the nudge running every
> 15 minutes, that's a lot of popups. Magisk app → Superuser tab →
> Termux → toggle the notification (bell) icon off (repeat for Tasker
> and Shell). See `docs/troubleshooting.md`.

### Install Termux

The Play Store version of Termux is outdated. Install from F-Droid or
GitHub:

```bash
# From F-Droid: https://f-droid.org/packages/com.termux/
# Or download APK: https://github.com/termux/termux-app/releases
```

### Install Python in Termux

Open Termux and run the following commands. Termux uses its own package
manager (`pkg`, which wraps `apt`) — do **not** use the Play Store
version of Termux, as it is outdated and its package repos are
discontinued.

```bash
# Update the package list
pkg update && pkg upgrade -y

# Install Python 3 and pip
pkg install -y python

# Verify installation
python --version   # should show Python 3.11 or newer
pip --version

# Install the requests library (required by wifi_portal_automation)
pip install requests

# Install git if you want to clone the repo directly on the phone
pkg install -y git
```

#### Notes

- **Python version**: Termux ships Python 3.11+ on current builds. If
  `python --version` shows an older version, run `pkg upgrade python`.
- **No virtualenv needed**: Termux's environment is already isolated
  from the Android system Python (if any). Installing packages globally
  with `pip` is fine in Termux.
- **Storage access**: If you need to access `/sdcard` from Termux
  (e.g., to copy files from your computer via `adb push`), run:
  ```bash
  termux-setup-storage
  ```
  This creates symlinks to shared storage under `~/storage/`.
- **requests dependencies**: `pip install requests` also installs
  `urllib3`, `certifi`, `charset-normalizer`, and `idna` automatically.

### Install Tasker Settings

See the [Tasker setup guide](tasker-android-setup.md) for Tasker
Settings installation instructions.

## Installation

### Option A: Clone the Repo (if git is available)

```bash
# In Termux:
pkg install git
cd ~    # Termux home — where the clone must live
git clone https://github.com/yaconsult/wifi-portal-automation.git ~/wifi-portal-automation
cd ~/wifi-portal-automation
pip install -e .
```

> The clone must live in Termux's private home (`~/`), not shared
> storage — `/sdcard` does not support the symlinks and file
> permissions an editable `pip install -e .` needs. The explicit
> `~/wifi-portal-automation` target puts it there regardless of where you run
> the command, but `cd ~` first keeps `git pull` habits consistent.
>
> Cloning is a **one-time** step. If `~/wifi-portal-automation` already exists,
> `git clone` fails with "already exists and is not an empty
> directory" — update instead:
>
> ```bash
> cd ~/wifi-portal-automation && git pull
> ```

### Option B: Copy Files Manually

If you can't clone from the phone, copy the source files from your
computer:

```bash
# From your computer:
adb push src/wifi_portal_automation/ /sdcard/wifi_portal_automation/
adb push pyproject.toml /sdcard/wifi_portal_automation/

# In Termux:
mkdir -p ~/wifi-portal-automation
cp -r /sdcard/wifi_portal_automation/* ~/wifi-portal-automation/
cd ~/wifi-portal-automation
pip install -e .
```

### Verify Installation

```bash
wpa-android --once --verbose
```

You should see logging output showing the WiFi-bound session being
created and portal detection running.

## Tasker Integration

Tasker handles WiFi detection and connection; Termux/Python handles
the captive portal. This section provides complete step-by-step
instructions for wiring up the full automatic flow.

### Prerequisites Checklist

Before starting, make sure you have all of these:

- [ ] **Termux** installed (from F-Droid)
- [ ] **Termux:Tasker** installed (from F-Droid)
- [ ] **Python 3.11+** installed in Termux (`pkg install python`)
- [ ] **requests** installed in Termux (`pip install requests`)
- [ ] **MVwifiAuto** cloned and installed in Termux (`pip install -e .`)
- [ ] **Tasker** installed (latest version)
- [ ] **Tasker Settings** helper app installed (required for WiFi
      connection on Android 10+ — see
      [tasker-android-setup.md](tasker-android-setup.md) for details)
- [ ] **cmvwifi** saved in Android WiFi settings with **auto-connect
      turned OFF** (connect once manually, accept the portal, then
      disable auto-connect)

### Import the Tasker Project (Optional)

The repo includes a pre-built Tasker XML for the Termux approach:

```
android/MVwifiAuto-Termux.prj.xml
```

This contains the `MVW_ConnectAndRun`, `MVW_RunPortalScript`,
`MVW_CostcoConnect`, `MVW_NudgeWifi`, `MVW_NudgeXfinity`, `MVW_ShowVersion`,
`MVW_SHGuestCapture`, and `MVW_UnlockNudge` tasks, plus the `MVW cmvwifi
Auto Connect`, `MVwifi
Auto Connect`, `MVW Costco WiFi Connected`, `MVW cmvwifi Periodic Nudge`,
`MVW xfinitywifi Periodic Nudge`, `MVW SHGuestNet WiFi Connected`, and
`MVW Display Unlocked Nudge`
profiles — all pre-configured for the Termux:Tasker plugin.

The `MVW SHGuestNet WiFi Connected` profile (healthcare clinic
guest WiFi) runs a *capture probe*, not an accept handler: on
association it saves the portal's redirect chain, HTML, form fields,
JS assets, and any deep-link intents to
`~/storage/shared/shguestnet_capture/capture_<ts>/` — the evidence
needed to build an auto-accept handler. `SHGuestNet` is in the
preferred tier (open), so the nudge also auto-joins it. Delete the
profile if you don't want captures.

Every task's history marker carries a generation stamp
(`[gen xxxxxx]` — a content hash of the XML) so `tail`ing the
history log reveals exactly which project generation is running; a
stale merged import shows an old or absent gen id. You can also run
the `MVW_ShowVersion` task manually (▶) to flash the imported project's
gen id and compare it against the one in the XML on `/sdcard`. The Costco profile
auto-accepts the Mist TOS portal on `Costco Member Wifi` (see
`docs/costco-portal-capture.md`); delete that profile if you don't
want it. The Periodic Nudge profiles require root and can
be deleted on unrooted devices (its wrapper exits cleanly with "no
root" — nothing else breaks).

To import it:

```bash
# Push to phone
adb push android/MVwifiAuto-Termux.prj.xml /sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml
```

Then in Tasker: long-press the bottom nav bar → **Import Project**
→ select `MVwifiAuto-Termux`.

**Optional but recommended — also import `MVwifiWatchdog.prj.xml`
once.** It's a separate project deliberately: it checks
`mvwifi_history.log`'s mtime every 6h and posts a notification if
nothing has written for >24h — exactly the failure mode where the
main project gets deleted during unrelated Tasker work (observed
Oct 13: ~12h of silent non-operation). Because it's its own
project, it survives that deletion. Regenerate with
`wpa-tasker-gen --watchdog`.

> **⚠️ Imports are held in memory until Tasker exits gracefully.**
> Tasker only writes its project state to disk on a clean exit —
> a force-stop or swipe-from-recents **silently discards a fresh
> import** (observed on-device: `MVW_ShowVersion` flashed the new gen
> id and manual task runs used new definitions, while scheduled
> ticks kept firing the *persisted* old defs; after a force-stop
> the GUI showed the old version too). After every import: back out
> of Tasker or use its exit action **before** rebooting or
> force-stopping, so the import actually persists.

> **⚠️ The full reimport procedure.** Four steps, all required:
>
> 1. **Delete the project tab** (long-press → Delete). Importing
>    over an existing project *merges* — old task definitions are
>    kept and only new elements added (observed: scheduled
>    `MVW_NudgeWifi` still invoked the renamed `cmvwifi_nudge` wrapper).
> 2. **Delete leftover tasks and profiles too.** Deleting the tab
>    leaves the tasks in Tasker's global list — a re-import then
>    *fails* with "the name MVW_ConnectAndRun already exists". In the
>    TASKS and PROFILES tabs, delete: `MVW_ConnectAndRun`,
>    `MVW_RunPortalScript`, `MVW_CostcoConnect`, `MVW_NudgeWifi`,
>    `MVW_NudgeXfinity`, `MVW_ShowVersion`, `MVW_SHGuestCapture`,
>    `MVW_UnlockNudge` and any leftover `*wifi*`/
>    `cmvwifi`/`Costco`/`Nudge` profiles.
> 3. **Import**, then **exit Tasker gracefully** (Back button or
>    its Exit menu item) so the new project is written to disk.
> 4. **Relaunch Tasker once** — imported contexts don't register
>    with the running monitor until the app opens (observed as
>    *three days* of silence that resolved the moment Tasker was
>    opened).
>
> Tasker stores live project data in internal app storage —
> pushing a new XML to `/sdcard` never updates the running project
> by itself. **Verify**: `MVW_ShowVersion` (▶) flashes the imported gen
> id — compare with `grep -o "gen [0-9a-f]*"
> /sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml`. The next
> scheduled tick's `[gen xxxxxx]` markers should match; an old gen
> means the import didn't take or wasn't persisted.

> **Note**: After import, you still need to create the wrapper script
> (Step 1) and grant the Termux:Tasker permission (Step 2). The XML
> only contains the Tasker tasks and profile — it can't create files
> in Termux or grant permissions.
>
> To regenerate the XML after editing:
> ```bash
> uv run python -m wifi_portal_automation.tasker_gen --termux
> ```

### Step 1: Create the Wrapper Script

Termux:Tasker looks for executable scripts in `~/.termux/tasker/`.
Create a wrapper that runs the Python portal handler:

```bash
# In Termux:
mkdir -p ~/.termux/tasker

# Find the full path to wpa-android
which wpa-android
# Should show: /data/data/com.termux/files/usr/bin/wpa-android

# Install the wrapper (single source of truth: android/mvwifi_portal)
# Termux:Tasker runs in a minimal environment without PATH, so the
# wrapper uses full paths throughout.
cp ~/wifi-portal-automation/android/mvwifi_portal ~/.termux/tasker/mvwifi_portal
chmod +x ~/.termux/tasker/mvwifi_portal
```

Test it works:

```bash
~/.termux/tasker/mvwifi_portal
echo "Exit code: $?"
```

> **Log files**: the detailed run log goes to
> `~/storage/shared/mvwifi_tasker.log`, overwritten each run, with
> the last line recording the outcome ("Run completed successfully"
> or "Run FAILED"). The wrapper also appends start/exit markers to
> `~/storage/shared/Tasker/mvwifi_history.log` (kept to the last 200
> lines; rotation runs under an atomic lock-dir since concurrent
> wrappers share the file) — Tasker writes its own marker there
> first, so a tasker
> marker with no termux line means the plugin call never reached
> Termux.

It should output logging lines showing interface detection and
portal handling. Exit code 0 means success.

> **Important**: The wrapper script must use the **full path** to
> `wpa-android` because the Termux:Tasker plugin runs in a minimal
> environment without the Termux PATH. Using just `wpa-android`
> will fail with "no such file".

### Step 2: Enable Termux:Tasker Integration

Two things must be enabled for Tasker to run Termux scripts:

#### 2a: Grant RUN_COMMAND permission to Tasker

Tasker needs permission to run commands in the Termux environment:

1. Open **Android Settings** → **Apps** → **Tasker**
2. Tap **Permissions**
3. Tap **Additional permissions** (may be under a "More" section)
4. Enable **Run commands in Termux environment**

If you don't see "Additional permissions" or "Run commands in Termux
environment", make sure:
- Tasker is version 5.9.3 or newer
- Termux:Tasker is installed and has been opened at least once
- Both Termux and Termux:Tasker are from F-Droid (not Play Store)

You can also grant it via adb:

```bash
adb shell pm grant com.joaomgcd.tasker com.termux.permission.RUN_COMMAND
```

#### 2b: Allow external apps in Termux

Termux must be configured to allow external apps (like Tasker) to run
commands:

```bash
# In Termux:
mkdir -p ~/.termux
echo "allow-external-apps = true" >> ~/.termux/termux.properties
```

Then **force-close Termux** (Settings → Apps → Termux → Force Stop)
and reopen it for the change to take effect.

> **Warning**: This allows any app with the `RUN_COMMAND` permission
> to execute commands in your Termux environment. Only grant the
> permission to apps you trust (like Tasker).

#### 2c: Verify the setup

Test that Tasker can run a simple Termux command:

1. In Termux, create a test script:
   ```bash
   echo '#!/data/data/com.termux/files/usr/bin/sh' > ~/.termux/tasker/test
   echo 'echo "Termux:Tasker works!" > /dev/null' >> ~/.termux/tasker/test
   chmod +x ~/.termux/tasker/test
   ```

2. In Tasker, create a test task with a **Plugin → Termux:Task**
   action, executable: `test`

3. Run the task — if it succeeds without error, the integration is
   working. If you get a permission error, go back and check 2a and 2b.

#### 2d: Protect Termux from Android power management

Android aggressively kills background apps. Tasker is usually
battery-exempt already, but **Termux is not by default** — if Android
kills Termux while a plugin command runs, Tasker reports
"plugin did not respond before timing out" (error code 2) even though
the script may have nearly finished.

Two protections are needed:

1. **Exempt Termux from battery optimization:**
   - Settings → Apps → Termux → Battery → **Unrestricted**
   - Or via adb: `adb shell dumpsys deviceidle whitelist +com.termux`

2. **Disable the phantom process killer** (Android 12+):
   Android kills "phantom" child processes (bash, python) spawned by
   background apps — exactly what Termux:Tasker executions are.
   ```bash
   adb shell settings put global settings_enable_monitor_phantom_procs false
   ```

Both settings persist in `/data` and survive OS updates that don't
wipe data, but **re-verify them after each system update**:

```bash
adb shell dumpsys deviceidle whitelist | grep termux
adb shell settings get global settings_enable_monitor_phantom_procs   # want: false
```

> **Symptom this prevents**: the profile fires, the script starts
> (log file appears), then dies mid-run and Tasker reports the plugin
> timeout error — even with a generous action timeout.

### Steps 3–6: Tasker Configuration (Manual Fallback)

Importing `MVwifiAuto-Termux.prj.xml` creates everything below
automatically — the generated XML is the source of truth
(`uv run python -m wifi_portal_automation.tasker_gen --termux` regenerates it).
Only recreate these by hand if you cannot import the XML.

**Task: `MVW_RunPortalScript`** — runs the Python script via the
Termux:Tasker plugin:

| # | Action | Fields |
|---|--------|--------|
| 1 | Plugin → Termux:Task | Executable `mvwifi_portal`; Arguments blank; Background ✓; **Timeout 60s** |

**Task: `MVW_ConnectAndRun`** — connects to cmvwifi (unless already on
it), self-heals Termux protections, runs the portal script:

| # | Action | Fields |
|---|--------|--------|
| 1 | File → Write File | File `Tasker/mvwifi_history.log`, Text `MVW_ConnectAndRun fired %TIMES`, Append ✓, Add Newline ✓ — marker proving Tasker fired |
| 2 | Variables → Variable Set | `%CurrentSSID` = `%WIFII` |
| 3 | Task → If | `%CurrentSSID` `~` `cmvwifi` |
| 4 | Task → Goto | Type **Action Number**, Number **8** |
| 5 | Task → End If | |
| 6 | Net → Connect to WiFi | SSID `cmvwifi` |
| 7 | Task → Wait | 5 seconds — DHCP needs a moment to assign an IP before the script binds to wlan0; raise to 10 if you see binding errors |
| 8 | Code → Run Shell | self-heal command below; Use Root ✓; Timeout 15s; **Continue Task After Error** ✓ (unrooted phones skip it) |
| 9 | Task → Perform Task | Name `MVW_RunPortalScript` |
| 10 | Alert → Flash | `Portal handling complete` |

A8 self-heal command:

```sh
dumpsys deviceidle whitelist +com.termux; settings put global settings_enable_monitor_phantom_procs false; pm grant com.termux android.permission.WRITE_SECURE_SETTINGS
```

> The A3–A5 If/Goto skips Connect+Wait when already on cmvwifi
> (e.g. walking back into range); the self-heal A8 runs either way
> so protections are re-applied every run. **Adjust the Goto
> number** if you add or remove actions — it targets the Run Shell
> step by position.

**Profile: `MVW cmvwifi Auto Connect`** — triggers `MVW_ConnectAndRun` the
moment the phone associates to cmvwifi:

- **PROFILES** → **+** → **State** → **Net** → **WiFi Connected**
- SSID `cmvwifi`, MAC blank, IP blank, Active: checked
- Enter task: `MVW_ConnectAndRun`

> We deliberately use **WiFi Connected**, not WiFi Near: Android
> auto-joins cmvwifi itself, and WiFi Near depends on WiFi scans
> that Android throttles to ~1 per 30 min for background apps —
> observed as a ~30-minute delay before portal handling. WiFi
> Connected fires instantly on association. The periodic root
> nudge covers the association step WiFi Near was meant to trigger
> — see "Why root + polling instead of Tasker's network contexts"
> in `docs/architecture.md` for the full tradeoff rationale.

### Step 7: Test the Full Flow

**Manual test (near cmvwifi):**

1. Turn WiFi off and on (to reset state)
2. In Tasker, tap the **MVW_ConnectAndRun** task to run it manually
3. Watch for:
   - WiFi connects to cmvwifi
   - 5-second wait
   - Python script runs (check with `--verbose` if needed)
   - Flash: "Portal handling complete"
4. Verify internet works (open a browser or app)

**Automatic test:**

1. Walk away from cmvwifi range, then walk back
2. Wait a few seconds — the profile fires as soon as the phone
   associates to cmvwifi (no scan delay)
3. The profile should trigger `MVW_ConnectAndRun` automatically
4. Check the Tasker run log (three dots menu → **View Run Log**) to
   confirm it executed

**Debugging with logs:**

The wrapper script already includes `--verbose --log-file` by default.
The log file is written to `~/storage/shared/mvwifi_tasker.log`,
**overwritten on each run**, and its last line records the outcome
("Run completed successfully" or "Run FAILED"). A log that ends
mid-run with no outcome line means the process was killed.

After a failed run, check the log:

```bash
cat ~/storage/shared/mvwifi_tasker.log
```

For a cross-run timeline, check the history log — it records every
Tasker trigger and every wrapper start/exit:

```bash
cat ~/storage/shared/Tasker/mvwifi_history.log
```

Or transfer either file via Google Drive / `adb pull` for easier reading.

### Step 8: Verify the Profile is Active

1. Open Tasker → **PROFILES** tab
2. The `MVW cmvwifi Auto Connect` profile should have a **green dot**
   next to it (active)
3. If it's greyed out, tap the profile to toggle it on

### Troubleshooting Tasker Integration

**"Termux:Task" not appearing in Tasker plugins:**
- Open Termux:Tasker app once (just launch it, then close)
- Open Termux once (just launch it, then close)
- Restart Tasker (force close and reopen)
- Check Tasker → Preferences → Plugin → Termux:Tasker is enabled

**Script runs but portal handling fails:**
- Run the wrapper script manually in Termux to see the error:
  ```bash
  ~/.termux/tasker/mvwifi_portal
  ```
- Check the log file (overwritten each run; last line shows the
  outcome, a missing outcome line means the process was killed):
  ```bash
  cat ~/storage/shared/mvwifi_tasker.log
  ```
- Make sure WiFi is connected to cmvwifi before the script runs
  (the 5-second wait should be enough, but try increasing it)

**"Connect to WiFi" returns Error 255:**
- You're already connected to cmvwifi — the If/Goto check in
  `MVW_ConnectAndRun` should handle this, but if it still happens, just
  run `MVW_RunPortalScript` directly

**It stopped working after working for a while:**
- Run `./scripts/verify_android.sh` from the PC with the phone
  connected via USB — it checks every prerequisite (battery
  exemption, phantom killer, permissions, wrapper, config) and
  reports which one regressed. OS updates can silently re-enable
  restrictions.

### After an Android system update

When flashing a monthly update (e.g. Google's `flash-all.sh`
edited to remove `-w`), `/data` survives so most settings persist —
but verify anyway, since an update can re-enable restrictions or
reset settings globals:

```bash
# Phone connected via USB, from the repo on the PC:
./scripts/verify_android.sh      # reports any regressed prerequisite
./scripts/deploy_android.sh      # re-applies everything (idempotent)
```

If `verify_android.sh` shows all PASS but connections still fail,
check `~/storage/shared/mvwifi_tasker.log` on the phone — its last
line records the outcome, and a missing outcome line means the run
was killed mid-execution.

Notes on what persists across a `-w`-less flash:
- Battery whitelist, phantom-killer setting, permission grants,
  and `termux.properties` all live in `/data` → survive
- The Tasker project (profiles, tasks, your UI edits) lives in
  Tasker's app data → survives; only re-import the XML if the
  project file changed — and if you do, follow the full procedure
  in the import section above: delete the tab *and* its leftover
  tasks/profiles, import, **exit gracefully to persist**, relaunch
- Even if the phantom-killer setting resets, the wrapper now
  re-applies it on every run (via the WRITE_SECURE_SETTINGS grant),
  so the system is self-healing once granted

### Updating MVwifiAuto after repo changes

A `git pull` on the phone does **not** update everything —
different components have different update paths:

| What changed in the repo | How to deploy it |
|---|---|
| `src/wifi_portal_automation/*.py` | `cd ~/wifi-portal-automation && git pull` in Termux — the editable install takes effect immediately |
| `termux_setup.sh`, `deploy_android.sh`, wrapper changes | Re-run `./scripts/deploy_android.sh` from the PC — it rewrites `~/.termux/tasker/mvwifi_portal` |
| `tasker_gen.py`, `android/MVwifiAuto-Termux.prj.xml` | `./scripts/deploy_android.sh` pushes the file, then re-import per the procedure above (delete tab + leftover tasks/profiles → import → graceful exit → relaunch) — the `/sdcard` file is only an import source, Tasker never re-reads it |

When in doubt, check `git log` for what changed since your last
update, deploy accordingly, then run `./scripts/verify_android.sh`
to confirm the on-device state is consistent.

**Profile doesn't trigger when connecting to cmvwifi:**
- WiFi Connected fires on association, not scans — it should be
  instant. Check that the profile is active (green dot in PROFILES
  tab)
- Make sure the profile's SSID is exactly `cmvwifi` and Active is
  checked
- Tasker needs Location permission for WiFi state contexts on
  Android 10+
- **If connecting takes ~30 min to trigger:** you have an old
  WiFi Near-based import — delete the project and re-import the
  current XML, which uses WiFi Connected (see Session 24+ of the
  devlog)

**Script not found:**
- Verify the wrapper script exists: `ls -la ~/.termux/tasker/mvwifi_portal`
- Verify it's executable: `chmod +x ~/.termux/tasker/mvwifi_portal`
- Verify `wpa-android` is in PATH: `which wpa-android`
- If not found, reinstall: `cd ~/wifi-portal-automation && pip install -e .`

## Usage

### Manual (from Termux)

```bash
# Run once with verbose output
wpa-android --once --verbose

# Run with custom interface
wpa-android --once --interface wlan1

# Run with more retry attempts
wpa-android --once --max-attempts 5
```

### Automatic (via Tasker)

Once the WiFi Connected profile is linked to `MVW_ConnectAndRun` (see the
Tasker Integration section above), it will trigger automatically the
moment the phone associates to cmvwifi.

### Fallback: Run Shell (without Termux:Tasker)

If you prefer not to use the Termux:Tasker plugin, you can use
Tasker's built-in Run Shell action instead. Replace the
`MVW_RunPortalScript` task's Plugin action with:

1. Add action: **Code** → **Run Shell**
2. **Command**: `/data/data/com.termux/files/usr/bin/wpa-android --once`
3. **Use Root**: No
4. **Timeout**: 30 seconds
5. **Store Output In**: `%portal_output` (optional, for debugging)

This works but runs in Tasker's minimal shell environment, which may
have PATH issues. The Termux:Tasker plugin is recommended.

## How It Works

1. **Tasker** detects the cmvwifi association via WiFi Connected and triggers `MVW_ConnectAndRun`
2. **MVW_ConnectAndRun** checks if already connected; if not, connects to
   cmvwifi via Tasker Settings, then waits 5 seconds for DHCP
3. **MVW_ConnectAndRun** calls `MVW_RunPortalScript` via the Termux:Tasker plugin
4. **Python** creates a `requests.Session` bound to wlan0:
   - `InterfaceBoundAdapter` sets `source_address` to wlan0's local IP
   - `SO_BINDTODEVICE` (socket option 25) forces the kernel to route
     packets through wlan0, bypassing Android's policy routing
   - Source IP binding alone is insufficient — Android ignores it and
     routes over cellular. `SO_BINDTODEVICE` is required.
5. **Python** detects the captive portal by GETting
   `http://detectportal.firefox.com/canonical.html`
   - The portal intercepts and returns a 302 redirect
   - The portal host is extracted from the redirect `Location` header
   - The host is dynamic (e.g. `10.64.2.24:9997`) and differs from the
     default gateway
6. **Python** POSTs to `http://<host>/forms/guest_toued` to accept terms
7. **Python** verifies internet connectivity via
   `http://detectportal.firefox.com/success.txt`

## Troubleshooting

### "Could not determine IPv4 address for interface 'wlan0'"

The interface name may differ on your device. The script auto-detects
the WiFi interface (tries wlan0, wlan1, wlan2, wlan), but you can
specify it manually:

```bash
wpa-android --once --interface wlan1
```

To find the correct interface name, run the diagnostic script:

```bash
python scripts/detect_interface.py
```

Note: `ip addr` does not work from Termux (Android blocks netlink
sockets). The ioctl-based detection in the script works fine.

### HTTP requests timing out (cellular is ON)

If requests hang for ~40 seconds and never complete, the interface
binding may not be taking effect. The script uses `SO_BINDTODEVICE`
for kernel-level binding, which should work from Termux without root.
To verify:

```bash
python scripts/test_bindtodevice.py
```

This tests whether `SO_BINDTODEVICE` works on your device. If it
reports "PERMISSION DENIED", your device may need root.

### Logging to a file for debugging

```bash
# Write to Termux home (no permissions needed)
wpa-android --once --verbose --log-file ~/mvwifi.log

# Write to shared storage (requires termux-setup-storage)
wpa-android --once --verbose --log-file ~/storage/shared/mvwifi.log
```

The log file is **overwritten on each run** — its last line records
the outcome ("Run completed successfully" or "Run FAILED"). If the
log ends mid-run with no outcome line, the process was killed
(e.g. Android power management — see the plugin-timeout entry).

Then transfer the log via Google Drive, `adb pull`, or `cat` and
copy from the Termux screen.

## Comparison: Termux vs Pure Tasker

| Aspect | Termux + Python | Pure Tasker |
|--------|----------------|-------------|
| Interface binding | Yes (SO_BINDTODEVICE + source IP) | No |
| Reliability | High (same code as laptop) | Race condition dependent |
| Root required | No (SO_BINDTODEVICE works as shell user) | No |
| Tasker Settings needed | Yes (for WiFi connection) | Yes |
| Tasker integration | Termux:Tasker plugin or Run Shell | Native HTTP Request |
| Code reuse | Full (shares captive_portal.py) | None (reimplemented in XML) |
| Testability | 260+ unit tests | XML generator tests only |
| Maintenance | Edit Python | Edit Python generator → regenerate XML |

## Files

Repo:

- `src/wifi_portal_automation/android.py` - Termux entry point
- `src/wifi_portal_automation/wifi_binding.py` - Interface-bound HTTP adapter
- `src/wifi_portal_automation/captive_portal.py` - Shared portal handling
- `src/wifi_portal_automation/root_shell.py` - Shared `su` discovery/execution
- `src/wifi_portal_automation/wifi_nudge.py` - Auto-join nudge (`cmd wifi` client)
- `src/wifi_portal_automation/tasker_gen.py` - Tasker XML generator (for WiFi Connected profile)
- `android/MVwifiAuto-Termux.prj.xml` - generated Tasker project
- `android/mvwifi_portal` - wrapper script (single source of truth)
- `android/costco_portal` - Costco Mist TOS-accept wrapper
- `android/costco_probe` - manual Costco portal capture wrapper
- `android/shguest_probe` - SHGuestNet portal capture wrapper
- `android/wifi_nudge` - periodic-nudge wrapper executed by Tasker (SSIDs via args)
- `android/cmvwifi_nudge` - compat shim → `wifi_nudge` (rescues stale task references)
- `scripts/termux_setup.sh` - on-device setup (runs in Termux)
- `scripts/deploy_android.sh` - full adb deployment (wrapper, XML, settings)
- `scripts/verify_android.sh` - on-device state checklist

On the phone:

- `~/wifi-portal-automation/` - repo clone (editable install target)
- `~/.termux/tasker/mvwifi_portal` - wrapper executed by Tasker
- `~/.termux/tasker/costco_portal` - Costco portal handler wrapper
- `~/.termux/tasker/costco_probe` - manual Costco capture wrapper
- `~/.termux/tasker/shguest_probe` - SHGuestNet capture wrapper
- `~/.termux/tasker/wifi_nudge` - nudge wrapper (Periodic Nudge profiles)
- `~/.termux/tasker/cmvwifi_nudge` - compat shim; tags shim invocations in history log
- `~/.termux/termux.properties` - `allow-external-apps = true`
- `~/storage/shared/mvwifi_tasker.log` - run log (overwritten each run)
- `~/storage/shared/mvwifi_nudge.log` - nudge detail log
- `~/storage/shared/costco_portal.log` - Costco handler log
- `~/storage/shared/shguestnet_capture/` - SHGuestNet portal captures
- `~/storage/shared/Tasker/mvwifi_history.log` - trigger/run history (last 200 lines)
- `/sdcard/Tasker/projects/MVwifiAuto-Termux.prj.xml` - XML import source
