# Tasker Android Setup Guide for MVwifiAuto

> ⚠️ **Legacy / reference path.** On modern Android (12+, verified
> broken on 16–17), source-IP binding alone cannot force HTTP
> requests over WiFi — Android's policy routing sends them over
> cellular anyway (detailed explanation:
> [Why Termux Over Pure Tasker?](android-termux-setup.md#why-termux-over-pure-tasker)).
> Use [android-termux-setup.md](android-termux-setup.md) instead: the
> Termux + Python approach binds with `SO_BINDTODEVICE` and reuses
> the laptop's working portal code. This document remains for
> reference and for older devices where pure Tasker still works.

## Overview

This setup automatically connects to `cmvwifi` when in range and handles the captive portal acceptance, similar to your laptop's MVwifiAuto service.

### What It Does
- Detects when `cmvwifi` is in range using WiFi Near
- Connects to the open network
- Accepts terms and conditions via HTTP POST
- Verifies internet connectivity
- Respects your home WiFi (`dd-wrt`) auto-connect

### Components Required
- 1 **Profile**: WiFi Near trigger for cmvwifi
- 2 **Tasks**: 
  - `ConnectToCmvwifi` - handles connection
  - `HandlePortal` - accepts terms and verifies

### XML Regeneration

The `android/MVwifiAuto.prj.xml` file is generated from testable Python
code. To regenerate after editing tasks:

```bash
uv run python -m wifi_portal_automation.tasker_gen --output android/MVwifiAuto.prj.xml
```

This eliminates the parameter-mapping bugs that occurred during
hand-editing (see android-devlog.md Sessions 14 and 17).

---

## How to Read This Guide

> **Formatting convention**: Values shown in `backticks` are exactly what you type into the Tasker field — **without** the backticks themselves. For example:
> - **Name**: `%DebugMode` → type `%DebugMode`
> - **URL**: `http://detectportal.firefox.com/canonical.html` → type that full URL
> - **Text**: `[HandlePortal] Starting portal handling` → type that exact text
>
> The backticks are just to visually separate the value from the surrounding sentence. Never type them.

---

## Prerequisites

Before starting:
1. Rooted Google Pixel with Magisk
2. Tasker installed (latest version)
3. **Tasker Settings** helper app installed (required for WiFi connection on Android 10+)
4. `cmvwifi` network saved in Android WiFi settings — **do not enable auto-connect**

### Install Tasker Settings

Google restricted WiFi connections for apps targeting newer APIs. Tasker works around this via a companion app targeting an older API:

1. Download: https://github.com/joaomgcd/TaskerSettings/releases/download/v1.3.0/TaskerSettings.apk
2. Install it (you may need to enable "Install unknown apps" for your browser)
3. Grant permissions:
   - Settings → Apps → **Tasker Settings** → Permissions → Location → **Allow all the time**
   - Settings → Apps → Tasker Settings → Battery → **Don't optimize**

> **Note**: Your phone may warn this app is built for an older Android version. This is **intentional and required** — the older API target is why it can still control WiFi. Dismiss the warning.

> **Why not auto-connect?** Android's built-in auto-connect would join `cmvwifi` but cannot accept the captive portal terms. You'd have a WiFi connection but no internet. Tasker's `ConnectToCmvwifi` task replaces auto-connect — it connects *and* immediately accepts the portal in sequence.
>
> **How to save `cmvwifi` without auto-connecting**: Go to Android **Settings → WiFi**, tap `cmvwifi`, connect once manually (accept the portal terms in the browser if prompted), then go back to the network settings and **turn off Auto-connect** for `cmvwifi`. After this one-time setup, Tasker handles all future connections.
>
> **Why it must be saved at all**: Tasker's **Net → Connect to WiFi** action requires the network to already exist in Android's saved networks list. If it has never been connected to, the action will fail with Error 1.

---

## Debug System Setup (Do This First)

All tasks share a single `DebugFlash` helper task. Instead of each task having its own `If %DebugMode / Flash / End If` block, they simply call `DebugFlash` with a message. `DebugFlash` handles the check internally — one place, one change to control all logging.

### Create Task: `DebugFlash`

This is the single shared logging function. It receives a message via `%par1` and only flashes it if `%DebugMode` is `true`.

1. **Tasks** tab → **+**
2. Name: `DebugFlash`
3. Tap **OK**
4. Tap **+** → **Task** → **If**
5. When prompted for type, select **"If"**
6. **Condition**: `%DebugMode` **~** `true`
7. Tap back

*Inside the If block:*

8. Tap **+** → **Alert** → **Flash**
9. **Text**: `%par1`
10. Tap back

*Close the If block:*

11. Tap **+** → **Task** → **End If**
12. Tap back

**How to call it from any other task:**
1. Tap **+** → **Task** → **Perform Task**
2. **Name**: `DebugFlash`
3. **Parameter 1 (`%par1`)**: your message, e.g. `[HandlePortal] Starting portal handling`
4. Tap back

That single action replaces the old 3-action If/Flash/End If pattern everywhere.

---

### Create Task: `DebugOn`

1. **Tasks** tab → **+**
2. Name: `DebugOn`
3. Tap **OK**
4. Tap **+** → **Variables** → **Variable Set**
5. **Name**: `%DebugMode`, **To**: `true`
6. Tap back
7. Tap **+** → **Alert** → **Flash**
8. **Text**: `Debug logging ON`
9. Tap back

### Create Task: `DebugOff`

1. **Tasks** tab → **+**
2. Name: `DebugOff`
3. Tap **OK**
4. Tap **+** → **Variables** → **Variable Set**
5. **Name**: `%DebugMode`, **To**: `false`
6. Tap back
7. Tap **+** → **Alert** → **Flash**
8. **Text**: `Debug logging OFF`
9. Tap back

Run `DebugOn` once before testing to enable debug messages. Run `DebugOff` when everything is working to silence all tasks.

**To enable debugging**: Run `DebugOn`
**To disable debugging**: Run `DebugOff`

---

## Step 1: Create the HandlePortal Task

This task accepts the captive portal terms. Create this first since it's called by the other task.

### Create the Task

1. Open **Tasker**
2. Tap the **TASKS** tab at the bottom
3. Tap the **+** button (usually bottom right)
4. Enter name: `HandlePortal`
5. Tap **OK**

### How Tasker If Blocks Work

> **Important**: When you tap **Task** → **If**, Tasker asks you to choose a type. Always select **"If"** (not "If / Else If / Else") unless you need an Else branch.
>
> After adding the **If** action, any action you add with **+** goes *inside* that If block until you add an **End If**. The **End If** closes the block. Any action added *after* **End If** runs unconditionally (outside the block).
>
> Tasker visually indents actions inside the block so you can see the structure.

### Add Actions

#### Action 1: Debug Entry

*Outside any block — always runs.*

1. Tap **+**
2. Select **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[HandlePortal] Starting portal handling`
5. Tap the **back arrow** ← to save

---

#### Action 2: Detect Captive Portal (HTTP Request with Redirects Disabled)

*This action is outside and after the If block — it always runs.*

1. Tap **+** to add an action
2. Select **Net** → **HTTP Request**
3. Configuration:
   - **Method**: `GET`
   - **URL**: `http://detectportal.firefox.com/canonical.html`
   - **Automatically Follow Redirects**: **UNCHECK this box** (critical for detecting the portal)
   - **File/Directory To Save With Output**: (leave blank)
   - **Timeout**: `10` seconds
4. Tap the **back arrow** ← to save

**Note**: After this action runs, Tasker automatically sets local variables:
- `%http_response_code` = response code (302, 307, 200, etc.)
- `%http_headers()` = response headers array (contains Location: header)
- `%http_data` = response body
- `%http_date` = response date

#### Action 3: Debug HTTP Response

*Outside any block — always runs after Action 2.*

1. Tap **+**
2. Select **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[HandlePortal] HTTP code: %http_response_code`
5. Tap the **back arrow** ← to save

---

#### Action 4: Check if Redirected (Captive Portal Detected)

*Outside any block — always runs. This is the main outer If block.*

1. Tap **+**
2. Select **Task** → **If**
3. When prompted for type, select **"If"**
4. **Condition**: `%http_response_code` **~** `302` **OR** `%http_response_code` **~** `307`
5. Tap the **back arrow** ← to save

**If this condition is not met (no redirect), Tasker skips to the End If at Action 14 automatically.**

*You are now inside the outer If block. All actions from here through Action 13 are inside it.*

#### Action 5: Debug Captive Portal Detected

*Inside outer If block (Action 4).*

1. Tap **+**
2. Select **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[HandlePortal] Captive portal detected`
5. Tap the **back arrow** ← to save

*Still inside the outer If block. Actions 6–8 go here.*

---

#### Action 6: Extract Location Header

*Inside outer If block (Action 4).*

1. Tap **+**
2. Select **Variables** → **Variable Set**
3. Configuration:
   - **Name**: `%LocationHeader`
   - **To**: `%http_headers()`
4. Tap the **back arrow** ← to save

#### Action 7: Remove "Location: http://" Prefix

*Inside outer If block.*

1. Tap **+**
2. Select **Variables** → **Variable Search Replace**
3. Configuration:
   - **Name**: `%LocationHeader`
   - **Search**: `Location: http://`
   - **Replace Matches With**: (leave blank)
4. Tap the **back arrow** ← to save

#### Action 8: Split to Get Gateway IP

1. Tap **+**
2. Select **Variables** → **Variable Split**
3. Configuration:
   - **Name**: `%LocationHeader`
   - **Splitter**: `/`
4. Tap the **back arrow** ← to save

*Now `%LocationHeader1` contains the gateway IP (e.g., `192.168.1.1`)*

> **Note — Variable Split creates an indexed array**: After splitting, Tasker replaces `%LocationHeader` with numbered elements: `%LocationHeader1`, `%LocationHeader2`, etc. The original unsuffixed `%LocationHeader` no longer holds the full string. Since the URL before the first `/` is the IP address, `%LocationHeader1` is always the gateway IP we need.

#### Action 9: Debug Gateway Extraction

*Inside outer If block (Action 4).*

1. Tap **+**
2. Select **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[HandlePortal] Gateway IP: %LocationHeader1`
5. Tap the **back arrow** ← to save

*Still inside the outer If block (Action 4). Action 10 goes here.*

---

#### Action 10: HTTP POST to Portal

*Inside outer If block (Action 4).*

1. Tap **+**
2. Select **Net** → **HTTP Request**
3. Configure:
   - **Method**: Select `POST`
   - **URL**: Enter `http://%LocationHeader1/forms/guest_toued`
   - **Headers**: `Content-Type:application/x-www-form-urlencoded`
   - **Body**: `origurl=http://www.google.com&ok=Accept and Continue`
   - **Timeout**: `10` seconds
   - **Follow Redirects**: Check this box
4. Tap the **back arrow** ← to save

#### Action 11: Wait for Processing

*Inside outer If block (Action 4).*

1. Tap **+**
2. Select **Task** → **Wait**
3. **Seconds**: `2`
4. Tap the **back arrow** ← to save

#### Action 12: Verify Internet (HTTP Success Test)

*Inside outer If block (Action 4).*

1. Tap **+**
2. Select **Net** → **HTTP Request**
3. Configure:
   - **Method**: `GET`
   - **URL**: `http://detectportal.firefox.com/success.txt`
   - **Automatically Follow Redirects**: Check this box
   - **File/Directory To Save With Output**: (leave blank)
   - **Timeout**: `5` seconds
4. Tap the **back arrow** ← to save

**Note**: After this action, `%http_response_code` will contain the response code (200 = success)

#### Action 13: If Internet Works (If/Else block)

*Inside outer If block (Action 4). This creates a nested If/Else.*

1. Tap **+**
2. Select **Task** → **If**
3. When prompted for type, select **"If / Else / End If"** (we need the Else branch)
4. **Condition**: `%http_response_code` **~** `200`
5. Tap the **back arrow** ← to save

*You are now inside the success (If) branch. Add the debug call:*

6. Tap **+**
7. Select **Task** → **Perform Task**
8. **Name**: `DebugFlash`
9. **Parameter 1 (`%par1`)**: `[HandlePortal] Success: Connected to cmvwifi with internet!`
10. Tap the **back arrow** ← to save

*Now add the Else branch. Tasker added it automatically — tap it to move into it:*

11. Tap **+**
12. Select **Task** → **Else**
13. Tap the **back arrow** ← to save

*You are now inside the failure (Else) branch:*

14. Tap **+**
15. Select **Task** → **Perform Task**
16. **Name**: `DebugFlash`
17. **Parameter 1 (`%par1`)**: `[HandlePortal] Failed: Portal acceptance failed`
18. Tap the **back arrow** ← to save

#### Action 14: End If (Closes internet check If/Else)

*Closes the If/Else from Action 13. Still inside outer If block (Action 4).*

1. Tap **+**
2. Select **Task** → **End If**
3. Tap the **back arrow** ← to save

#### Action 15: End If (Closes outer captive portal If block)

*This closes the outer If block from Action 4. Back outside all blocks.*

1. Tap **+**
2. Select **Task** → **End If**
3. Tap the **back arrow** ← to save

*The task is now complete.*

### HandlePortal Task Summary

Your task should show:
```
A1:  Perform Task [ Name:DebugFlash Par1:[HandlePortal] v19 starting ]
A2:  Wait [ Seconds:3 ]
A3:  HTTP Request [ Method:GET URL:http://1.1.1.1/ Follow Redirects:On Timeout:10 ]
A4:  Variable Search Replace [ Var:%http_response_url Search:http://([^/]+)/.* Result:%PortalHost ]
A5:  Perform Task [ Name:DebugFlash Par1:Portal: %PortalHost ]
A6:  HTTP Request [ Method:POST URL:http://%PortalHost/forms/guest_toued Headers:Content-Type:application/x-www-form-urlencoded Body:origurl=http%3a%2f%2f1%2e1%2e1%2e1%2f&ok=Accept+and+Continue Timeout:10 ]
A7:  Perform Task [ Name:DebugFlash Par1:POST: %http_response_code ]
A8:  HTTP Request [ Method:GET URL:http://detectportal.firefox.com/success.txt Timeout:10 ]
A9:  Variable Set [ Name:%VerifyResult To:%http_response_code ]
A10: If [ %VerifyResult ~ 200 ]
A11:   Perform Task [ Name:DebugFlash Par1:[HandlePortal] Success: Connected with internet! ]
A12: Else
A13:   Perform Task [ Name:DebugFlash Par1:[HandlePortal] Failed: Portal acceptance failed ]
A14: End If
```

> **Note**: The portal host is extracted dynamically from the redirect
> URL using `Variable Search Replace` with regex `http://([^/]+)/.*`.
> This is necessary because the portal IP changes between sessions
> (e.g. `10.64.2.21:9997` vs `10.64.2.23:9997`).

---

## Step 2: Create the ConnectToCmvwifi Task

This task connects to cmvwifi and calls HandlePortal.

### Create the Task

1. In **TASKS** tab, tap **+**
2. Enter name: `ConnectToCmvwifi`
3. Tap **OK**

### Add Actions

#### Action 1: Debug Entry

1. Tap **+**
2. Select **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[ConnectToCmvwifi] cmvwifi detected, connecting...`
5. Tap the **back arrow** ← to save

#### Action 2: Connect to WiFi

1. Tap **+**
2. Select **Net** → **Connect to WiFi**
3. Configure:
   - **SSID**: `cmvwifi`
4. Tap the **back arrow** ← to save

> **Note**: `cmvwifi` must have been connected to manually at least once first so Android has it in its saved networks list. This action requires the **Tasker Settings** helper app (see Prerequisites) to work on Android 10+.

#### Action 3: Debug Connection Status

1. Tap **+**
2. Select **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[ConnectToCmvwifi] Connected, waiting for portal...`
5. Tap the **back arrow** ← to save

#### Action 4: Wait

1. Tap **+**
2. Select **Task** → **Wait**
3. **Seconds**: `3`
4. Tap the **back arrow** ← to save

#### Action 5: Call HandlePortal

1. Tap **+**
2. Select **Task** → **Perform Task**
3. **Name**: `HandlePortal`
4. Tap the **back arrow** ← to save

#### Action 6: End If

1. Tap **+**
2. Select **Control** → **End If**
3. Tap the **back arrow** ← to save

### ConnectToCmvwifi Task Summary

```
A1: Perform Task [ Name:DebugFlash Par1:[ConnectToCmvwifi] cmvwifi detected, checking connection... ]
A2: If [ %WIFII ~ cmvwifi ]
A3:   Perform Task [ Name:DebugFlash Par1:[ConnectToCmvwifi] Already connected, handling portal... ]
A4:   Perform Task [ Name:HandlePortal ]
A5: Else
A6:   Net → Connect to WiFi [ SSID:cmvwifi ]
A7:   Perform Task [ Name:DebugFlash Par1:[ConnectToCmvwifi] Connected, waiting for portal... ]
A8:   Wait [ Seconds:3 ]
A9:   Perform Task [ Name:HandlePortal ]
A10: End If
```

> **Logic**: If already connected to cmvwifi, skip the connection step and go straight to portal handling. This prevents Error 255 when the network is already active.

---

## Step 3: Create the WiFi Near Profile

This profile triggers when cmvwifi comes in range.

### Create the Profile

1. Tap the **PROFILES** tab at the bottom
2. Tap the **+** button
3. Select **State** (not Time or Event)
4. Select **Net** → **WiFi Near**

### Configure WiFi Near

1. **SSID**: Enter `cmvwifi`
2. **MAC**: Leave blank
3. **Toggle**: Make sure it's set to detect when NEAR (not when NOT near)
4. Tap the **back arrow** ← to save

### Link to Task

After saving the context, you'll see "Enter Task Name":

1. Select **New Task** (if you already created ConnectToCmvwifi, you can select it from the list instead)
2. If New Task: Enter name `ConnectToCmvwifi`
3. Tap **OK**

**Note**: If you already created the ConnectToCmvwifi task in Step 2, you can simply select it from the list instead of creating a new task.

### Profile Structure

Your profile should show:
```
Profile: cmvwifi Auto Connect
  State: WiFi Near [ SSID:cmvwifi ]
Enter Task: ConnectToCmvwifi
```

---

## Step 3.5: (Optional) Test WiFi Scanning at Home

Since you don't have access to `cmvwifi` at home, you can create a test task to verify WiFi scanning works with your home network (`dd-wrt`). This confirms the scanning logic before you're near `cmvwifi`.

### Create Test Task

1. **Tasks** tab → **+**
2. Name: `TestWiFiScan`
3. Tap **OK**

### Add Actions

#### Action 1: Debug Entry

1. Tap **+**
2. **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[TestWiFiScan] Starting WiFi scan test`
5. Tap back

#### Action 2: Check Current WiFi

1. Tap **+**
2. **Variables** → **Variable Set**
3. **Name**: `%CurrentSSID`, **To**: `%WIFII`
4. Tap back

#### Action 3: Debug Current Network

1. Tap **+**
2. **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[TestWiFiScan] Current network: %CurrentSSID`
5. Tap back

#### Action 4: Check if on dd-wrt

*This is an If block. If `%CurrentSSID` matches, it runs the actions inside and stops.*

1. Tap **+**
2. **Task** → **If**
3. When prompted for type, select **"If"**
4. **Condition**: `%CurrentSSID` **~** `*dd-wrt*`
5. Tap back

*Inside the If block:*

6. Tap **+**
7. **Task** → **Perform Task**
8. **Name**: `DebugFlash`
9. **Parameter 1 (`%par1`)**: `[TestWiFiScan] Home network dd-wrt detected - scanning works!`
10. Tap back

11. Tap **+**
12. **Task** → **Stop**
13. Tap back

*Close the If block:*

14. Tap **+**
15. **Task** → **End If**
16. Tap back

*Back outside all blocks. Action 5 goes here.*

#### Action 5: Scan for Available Networks

*Outside any block — only reached if NOT on dd-wrt.*

1. Tap **+**
2. **Net** → **WiFi Near** (scan nearby networks)

*Alternatively use **Code** → **Run Shell** (requires root):*
- **Command**: `su -c "iw dev wlan0 scan | grep SSID"`
- **Store Output In**: `%AvailableNetworks`
- **Use Root**: Yes

#### Action 6: Debug Scan Results

1. Tap **+**
2. **Task** → **Perform Task**
3. **Name**: `DebugFlash`
4. **Parameter 1 (`%par1`)**: `[TestWiFiScan] Found networks: %AvailableNetworks`
5. Tap back

### TestWiFiScan Task Summary

```
A1: Perform Task [ Name:DebugFlash Par1:[TestWiFiScan] Starting WiFi scan test ]
A2: Variable Set [ Name:%CurrentSSID To:%WIFII ]
A3: Perform Task [ Name:DebugFlash Par1:[TestWiFiScan] Current network: %CurrentSSID ]
A4: If [ %CurrentSSID ~ *dd-wrt* ]
A5:   Perform Task [ Name:DebugFlash Par1:[TestWiFiScan] Home network dd-wrt detected! ]
A6:   Stop
A7: End If
A8: WiFi Scan / Run Shell [ Store Result In:%AvailableNetworks ]
A9: Perform Task [ Name:DebugFlash Par1:[TestWiFiScan] Found networks: %AvailableNetworks ]
```

### How to Use

1. Run this task manually (tap **Play**) while at home
2. You should see Flash messages showing your current network (`dd-wrt`)
3. If on `dd-wrt`, it confirms detection works and stops
4. If not on `dd-wrt`, it scans and shows nearby networks

This verifies the scanning logic works before you test with `cmvwifi`.

---

## Alternative: Import from XML (Skip Manual Setup)

Instead of building all tasks by hand, you can import the pre-built project file from the repo. This creates a new **MVwifiAuto** project tab in Tasker — your existing Base project tasks are not affected.

> **Important — task names are global in Tasker**: Task names must be unique across *all* projects. If you already have tasks named `ConnectToCmvwifi`, `HandlePortal`, etc. in your Base project, the import will fail with a conflict error. Delete or rename those tasks in Base before importing.

### Step 1: Transfer the file to your phone

**Option A — ADB (recommended if you have ADB set up):**
```
adb shell "rm -f /sdcard/Tasker/projects/MVwifiAuto.prj.xml" && adb push android/MVwifiAuto.prj.xml /sdcard/Tasker/projects/MVwifiAuto.prj.xml
```
> Note: This only updates the file on `/sdcard`. You still need to delete + reimport inside Tasker (Step 2) — Tasker's live project data lives in internal app storage which adb cannot modify.

**Option B — USB file transfer:**
1. Connect your phone via USB
2. On the phone, swipe down → tap the USB notification → select **File Transfer**
3. On your computer, copy `android/MVwifiAuto.prj.xml` to the phone's `Downloads` folder (or anywhere you can find it)

**Option C — Cloud/email:**
Copy the file to Google Drive, email it to yourself, or use any other method — just note where it lands on the phone.

### Step 2: Import into Tasker

> **⚠️ Always delete the existing project first.** Tasker stores its live data in internal app storage — the `/sdcard/Tasker/projects/` file is only an import source. Pushing a new XML file does **not** update the running project. You must delete and reimport every time.

**Delete the old project (if it exists):**
1. Open **Tasker** on the phone
2. Long-press the **MVwifiAuto** tab at the bottom
3. Tap **Delete Project** → confirm

**Then import the new one:**
1. **Long-press** on an empty area of the bottom bar (not on a tab name)
2. A menu appears — tap **Import Project**
3. A file browser opens — navigate to `Tasker/projects/`
4. Tap `MVwifiAuto.prj.xml` to select it
5. Tasker imports and a new **MVwifiAuto** tab appears at the bottom

### Step 3: Verify the import

Tap the **MVwifiAuto** tab. You should see:

**TASKS tab:**
- `ConnectToCmvwifi`
- `DebugFlash`
- `DebugOff`
- `DebugOn`
- `HandlePortal`
- `TestWiFiScan`

**PROFILES tab:**
- `cmvwifi Auto Connect` (linked to `ConnectToCmvwifi`)

Tap any task to open it and verify the actions look correct before running anything.

> **Verify the profile linkage**: On the PROFILES tab, tap `cmvwifi Auto Connect` and confirm the linked task shown is `ConnectToCmvwifi` — not `DebugFlash` or anything else. If it's wrong, tap the task name and change it.

### Step 4: Clean up existing tasks (if needed)

If you previously created any of these tasks manually in the **Base** project, you'll have duplicates. To delete them from Base:

1. Tap the **Base** tab
2. Long-press the task you want to remove
3. Tap the **trash icon** (delete)

### Step 5: After import setup

1. Tap the **MVwifiAuto** tab
2. Tap `DebugOn` then tap **▶ Play** at the bottom — you should see a Flash: `Debug logging ON`
3. This sets `%DebugMode` to `true` so all subsequent tasks will show debug messages

> **Note**: Global variables like `%DebugMode` are not stored in the XML. You must run `DebugOn` after every fresh import or phone restart (if Tasker loses its variable state).

---

## Step 4: Configure Profile Settings

### Enable the Profile

1. On the **PROFILES** tab, find your new profile
2. Make sure the toggle on the left is **ON** (green/blue)
3. If it shows a red slash, tap it to enable

### Battery Optimization (Important)

Since you're rooted, disable battery optimization for Tasker:

1. Android **Settings** → **Apps** → **Tasker**
2. **Battery** → **Battery Optimization**
3. Select **Don't optimize**

This ensures Tasker can monitor WiFi even when the screen is off.

---

## Step 5: Testing

### Test HandlePortal First

Before relying on the automation, test the portal handler:

1. Manually connect to `cmvwifi` through Android WiFi settings (don't accept terms yet)
2. Go to **TASKS** tab
3. Tap on `HandlePortal`
4. Tap the **Play** button (▶️) at the bottom
5. Watch for the Flash notifications
6. Check if you get "Connected to cmvwifi with internet!"
7. Open a browser and verify internet works

If it fails:
- Check the gateway IP in the Flash message
- Try visiting the portal manually in a browser to see the current URL
- The portal page might have changed - check what URL it redirects to

### Test Full Automation

1. Disconnect from `cmvwifi` in Android WiFi settings
2. Walk to an area where `cmvwifi` is available (or just wait if you're already there)
3. Wait up to 60 seconds for the WiFi Near state to detect it
4. You should see:
   - "cmvwifi detected, connecting..."
   - "Connected to cmvwifi with internet!"
5. Verify internet works in browser

### Test at Home

1. Connect to your home `dd-wrt` network
2. Verify the profile does NOT trigger
3. The profile should only activate when `cmvwifi` is in range

---

## Troubleshooting

### Profile Doesn't Trigger

| Issue | Solution |
|-------|----------|
| Profile shows disabled | Tap the toggle to enable it |
| WiFi is off | Turn on WiFi in Android settings |
| Not detecting cmvwifi | Check if cmvwifi appears in phone's WiFi list |
| Battery optimization | Disable for Tasker in Android settings |
| Out of range | Move closer to a cmvwifi access point |

### Connects but No Internet

| Issue | Solution |
|-------|----------|
| Wrong gateway IP | Check the Flash message for `%LocationHeader1` - it should be an IP like `192.168.1.1`. Also verify `%http_headers()` contains the Location header |
| HTTP Request fails | Remove `http://` from the URL if the variable already includes it |
| Portal changed | Visit portal manually and check the actual POST URL |
| Terms format changed | Inspect the portal form to see if field names changed |

### General Issues

| Issue | Solution |
|-------|----------|
| Tasker stops working | Restart Tasker, check if profile is still enabled |
| Flash messages not showing | Check if Tasker has notification permissions |
| Can't find WiFi Near | Make sure you selected **State** → **Net** → **WiFi Near** (not WiFi Connected) |

---

## Maintenance

### If Portal Stops Working

The city may change the portal page. To fix:

1. Connect to `cmvwifi` manually
2. Open browser and go to `http://detectportal.firefox.com/canonical.html`
3. Note the URL it redirects to
4. Check the form fields (use browser inspector)
5. Update the **HTTP Request** URL and Body in the `HandlePortal` task

### Monitoring

To see when the automation runs:
1. Open **Tasker** → **More** (three dots) → **Run Log**
2. This shows when profiles triggered and tasks ran

---

## Summary

You now have:
- **Profile**: `cmvwifi Auto Connect` - detects when cmvwifi is near
- **Task**: `ConnectToCmvwifi` - connects and calls portal handler
- **Task**: `HandlePortal` - accepts terms and verifies internet

The system runs automatically:
- At home on `dd-wrt`: No action (profile doesn't trigger)
- Near `cmvwifi`: Auto-connects and accepts terms
- On other networks: No action (profile only watches for cmvwifi)

---

## Notes

- The profile uses **WiFi Near** which polls periodically - there may be a 30-60 second delay before it detects cmvwifi
- Since you're rooted, this runs reliably even with screen off
- The portal acceptance uses the same HTTP POST as your laptop's MVwifiAuto
- If you want to disable temporarily, toggle off the profile in Tasker
