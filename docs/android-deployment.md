# Deploying to Android via USB (adb)

Everything this project puts on a rooted Android phone travels over
USB with `adb` — code, Tasker projects, wrapper scripts, config, and
the phone-side Python install itself. This doc collects the transfer
patterns; `scripts/deploy_android.sh` automates the full flow.

## Prerequisites

- Phone with USB debugging enabled (`adb devices` shows it)
- **Root** — Magisk/`su`, required by `cmd wifi` and by writing into
  Termux's private directory
- Termux + the **Termux:Tasker** plugin app installed (both free on
  F-Droid; the Tasker plugin needs
  `com.termux.permission.RUN_COMMAND` granted to Tasker)
- Tasker

Optional but recommended: in Magisk's Superuser tab, toggle the
notification (bell) icon off for Termux, Tasker, and Shell — otherwise
every scheduled `su` call pops a "granted superuser rights" toast
(see `docs/lessons-learned.md`).

If multiple devices show in `adb devices` (USB + WiFi ADB), pin every
command: `adb -s <serial> ...`.

## The three destinations

| What | Phone destination | How it gets there |
|---|---|---|
| Python package | Termux home (`~` inside Termux = `/data/data/com.termux/files/home`) | `git clone` + `pip install -e .` *on the phone* |
| Tasker project XML | `/sdcard/Tasker/projects/*.prj.xml` | `adb push` |
| Termux:Tasker wrappers | `~/.termux/tasker/` (Termux-private) | `adb push` to `/sdcard`, then `su -c 'cp …'` |

The `/sdcard` staging hop matters: `/sdcard` is FUSE-mounted
world-accessible, while `~/.termux/` lives under Termux's private
app data — `adb push` can't reach it directly. Push to `/sdcard`
first, then move with `su`.

## 1. The Python package — clone *on the phone*

The package must run in Termux's Python, so it gets its own clone:

```bash
adb shell su   # or adb shell, then su
cd ~ && git clone https://github.com/yaconsult/wifi-portal-automation.git
cd wifi-portal-automation && pip install -e .
```

`scripts/termux_setup.sh` does this plus Termux hardening (wake-lock
perm, battery-optimization exemption, `allow-external-apps`,
phantom-process-killer disable — see the script for the full list;
several steps need root or `WRITE_SECURE_SETTINGS`).

**Editable install + `python -m` is deliberate:** `git pull` then
picks up new code with no reinstall, and wrappers call
`python -m wifi_portal_automation.<module>` rather than entry-point
scripts (which only exist at install time).

## 2. Tasker project XML — push + import

```bash
# Generate (or use the shipped example)
uv run wpa-tasker-gen --termux --output android/MyProject.prj.xml
adb push android/MyProject.prj.xml /sdcard/Tasker/projects/
```

Then *on the phone*: Tasker → long-press bottom nav → **Import
Project** → pick the file. Critical import semantics discovered the
hard way (details in `docs/lessons-learned.md`):

- **Imports merge, never replace** — delete the old project tab AND
  its leftover tasks/profiles first, or stale definitions survive.
- **State persists only on graceful exit** — force-stop/swipe-away
  silently discards a fresh import. Back/Exit, relaunch, verify.
- Task names are global across projects — prefix yours (`MVW_` in
  the example) so unrelated project work can't collide.

## 3. Termux:Tasker wrappers — the /sdcard hop

```bash
TERMUX_HOME=/data/data/com.termux/files/home
for name in wifi_nudge mvwifi_portal costco_portal costco_probe shguest_probe cmvwifi_nudge; do
  adb push "android/$name" "/sdcard/$name.tmp"
  adb shell "su -c 'cp /sdcard/$name.tmp $TERMUX_HOME/.termux/tasker/$name && \
      chmod 755 $TERMUX_HOME/.termux/tasker/$name && rm /sdcard/$name.tmp'"
done
```

Wrappers must be **executable** and owned by the Termux user
(`scripts/deploy_android.sh` resolves the uid via
`stat -c %u $TERMUX_HOME` and `chown`s).

## 4. Network config (optional)

The tools run fine on the built-in example SSIDs; real deployments
push a `networks.toml` so `--preferred`/`--defer-to-preferred`
resolve to *your* networks without touching Tasker args:

```bash
adb push networks.toml /sdcard/networks.toml
adb shell "su -c 'mkdir -p $TERMUX_HOME/.config/wifi-portal-automation && \
    cp /sdcard/networks.toml $TERMUX_HOME/.config/wifi-portal-automation/ && \
    rm /sdcard/networks.toml'"
```

`wifi_portal_automation.config` auto-discovers
`~/.config/wifi-portal-automation/networks.toml` — no flag needed on
the phone.

## Verify the deployment

`scripts/verify_android.sh` checks the whole chain: rooted adb,
Termux package import, wrapper presence/permissions, XML on
`/sdcard`, Termux battery/phantom exemptions. Run it after any
deploy.

To verify a *Tasker import* (the only step adb can't do for you):
run the example project's `MVW_ShowVersion` task in Tasker — it
flashes the generated `gen xxxxxx` stamp, which should match the XML
you pushed.

## Log retrieval — reading what the phone did

The wrappers write plain-text logs you can pull or read live:

```bash
adb shell "su -c 'tail -30 /sdcard/Tasker/mvwifi_history.log'"   # tasker + wrapper lifecycle
adb shell "su -c 'tail -30 /sdcard/mvwifi_nudge.log'"            # nudge decisions
adb shell "su -c 'tail -30 /sdcard/Tasker/log/runlog.txt'"       # Tasker's own run log
```

`runlog.txt` is the authoritative "did Tasker actually fire it and
did the action error" source — `A Err` / `T ExitErr` lines are how
we found the Run Shell exit-code bug described in
`docs/lessons-learned.md`.
