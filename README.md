# wifi-portal-automation

Auto-connect to open WiFi networks and complete captive portals —
on Linux desktops and on **rooted Android** via Tasker + Termux.

This is a working reference implementation, not a toy example: the
network-selection logic, portal handling, and Tasker project it
generates run daily on real devices. Three hard problems it solves
that are barely documented anywhere else:

1. **Forcing HTTP over WiFi while cellular is up.** Android's policy
   routing prefers "validated" networks — your portal requests to
   WiFi-only IPs silently fail while cellular data is enabled.
   `wifi_binding.py` uses `SO_BINDTODEVICE` to pin the `requests`
   session to the WiFi interface. If you take one technique from
   this repo, take this one.

2. **Captive portal protocol handling that doesn't assume the
   gateway.** `captive_portal.py` probes a canary URL, follows the
   redirect chain to discover the real portal host (Costco's is a
   Juniper Mist CDN, not the store router), parses the TOS form —
   hidden fields, checkbox, submit — POSTs it, and re-probes to
   *verify* actual internet. See `docs/portal-types.md` for the
   field taxonomy of portal flavors.

3. **Reliable unattended automation on rooted Android.** A Tasker
   project *generator* (`tasker_gen.py`) that emits XML for
   join/portal/nudge/watchdog tasks, plus the operational knowledge
   that makes it survive real usage: import semantics, scan
   throttling, Termux hardening, watchdog-for-silent-deletion.
   That's `docs/lessons-learned.md`.

## The pieces

| Component | What it does |
|---|---|
| `wifi_binding.py` | `requests.Session` bound to a WiFi interface via `SO_BINDTODEVICE` (+ source-IP fallback), with `wlan0`/`wlan1` auto-detect |
| `captive_portal.py` | canary probe → redirect-derived portal host → form accept → internet verify |
| `portal_probe.py` / `portal_analyzer.py` | recon harness for unknown portals: captures redirect chains, forms, JS, deep links |
| `costco_portal.py` | worked example handler: Costco's Juniper Mist TOS portal |
| `controller.py` / `network_manager.py` | Linux daemon: NetworkManager-based decision loop + systemd units |
| `wifi_nudge.py` | Android join policy under root: ranked preferred/fallback SSIDs, never-steal rule, throttled triggers |
| `tasker_gen.py` | generates complete Tasker project XML (profiles, tasks, events, watchdog) from a data model |
| `android/` | Termux:Tasker wrapper scripts + example generated XML |
| `config.py` | `networks.toml` loading — your SSID list lives outside the code |

## The example networks

The SSIDs throughout the code, config, and docs are the real
networks this toolkit was built against — kept because concrete
examples beat placeholders:

| SSID | What it is | Portal class |
|---|---|---|
| `cmvwifi` | City of Mountain View's free community WiFi — broadcast at the public library, parks, and downtown | form-acceptance (button submit, no checkbox) |
| `MVwifi` | the same municipal network under an alternate SSID | form-acceptance |
| `Costco Member Wifi` | retail store guest WiFi (Juniper Mist portal) | form-acceptance (checkbox + submit) |
| `SHGuestNet` | a healthcare system's guest WiFi | MAC-remembered — accept once manually, never reprompts |
| `dd-wrt` / `dd-wrt_5G` | secured home networks | none — left to the OS selector |
| `xfinitywifi` | Comcast's subscriber hotspot network | carrier-provisioned — authorized out-of-band |

Your own list goes in `networks.toml` — see "Configure" below.

## Portal taxonomy cheat sheet

Full detail in `docs/portal-types.md`:

- **Form-acceptance** ("check box, click accept") — automatable:
  parse + POST. *Examples: cmvwifi, Costco Member Wifi.*
- **Credential** (account login) — out of scope by design; log in
  manually once.
- **Carrier/subscriber-provisioned** — already authorized at the
  account layer; just associate. *Example: xfinitywifi.*
- **MAC-remembered** — accept once manually, portal never reappears
  (even at other sites on the same backend). *Example: SHGuestNet.*
- **App/deep-link** — recon-only.

**Capture before you build**: `wpa-portal-probe` tells you which
class you're facing — you may find there's nothing to automate.

## Quick start — Linux

```bash
uv sync
uv run wpa-auto --once            # one connection cycle (NetworkManager)
uv run wpa-auto --daemon          # continuous; systemd units in systemd/
```

## Quick start — rooted Android (Tasker + Termux)

Prerequisites: rooted phone, Termux + Termux:Tasker (F-Droid), Tasker.

```bash
# 1. Termux-side setup — run inside Termux on the phone
git clone https://github.com/yaconsult/wifi-portal-automation.git
cd wifi-portal-automation && pip install -e .

# 2. From the desktop over USB
./scripts/deploy_android.sh        # pushes wrappers + XML, sets permissions

# 3. On the phone, in Tasker: long-press nav → Import Project →
#    MVwifiAuto-Termux.prj.xml → graceful exit → relaunch
```

Full instructions: `docs/android-termux-setup.md`.
The USB/adb transfer patterns: `docs/android-deployment.md`.

## Configure for your networks

Copy `networks.example.toml` to `networks.toml`, edit the SSID lists,
and either pass `--config` or drop it at
`~/.config/wifi-portal-automation/networks.toml` (auto-discovered —
this is how the phone-side wrappers pick it up with no Tasker-arg
plumbing):

```toml
preferred       = ["MyLibraryWiFi", "MyHomeAP"]   # outrank fallback
preferred_open  = ["MyLibraryWiFi"]               # joinable, no passphrase
fallback        = "CarrierHotspot"                # open, last resort
```

Regenerate the Tasker XML for your names with `wpa-tasker-gen` — or
treat `tasker_gen.py` as the example and adapt the builder functions;
the TaskerAction/TaskerProfile/XML-serialization layer is the
reusable machinery.

## Requirements

- Python ≥ 3.11, `requests` (only runtime dep); `uv` for dev
- Linux: NetworkManager (`nmcli`)
- Android: Termux, Termux:Tasker, Tasker — **root only for some features** (below)

## Do you need root?

Less than you'd think — the toolkit deliberately keeps the
portal-handling path root-free so unrooted phones still get partial
value:

| Capability | Root needed? | Why |
|---|---|---|
| Portal detect + accept (`wifi_binding`/`captive_portal`) | **No** | `SO_BINDTODEVICE` works from Termux's shell user — it's a socket opt, not a privileged call |
| Portal recon (`wpa-portal-probe`) | No | same bound-session mechanism |
| Tasker profiles → Termux:Tasker wrappers | No | the plugin runs as Termux's uid |
| `cmd wifi` joins / SSID ranking (`wpa-nudge`) | **Yes** | `cmd wifi connect-network`/`start-scan` require `su` |
| Auto-promotion off fallback networks | Yes | same `cmd wifi` mechanism |
| Deploying wrappers into Termux's private dir | Yes* | `~/.termux/` is app-private; `su` (or manual Termux-side copy) required |
| Phantom-process-killer toggle | Yes* | needs root or a `WRITE_SECURE_SETTINGS` grant |

*Unrooted path*: the generated Tasker project's portal profiles
(`WiFi Connected` → portal handler) work fully without root — join
the network yourself (or let Android), and terms still get accepted
automatically. What root adds is *automation of the join decision*:
ranked networks, fallback joins, never-steal, promotion. The nudge
wrapper exits cleanly with `no_root` on unrooted devices, so a mixed
setup degrades rather than breaks.

## Docs

- `docs/portal-types.md` — captive portal taxonomy + recon workflow
- `docs/android-deployment.md` — USB/adb code→phone transfer patterns
- `docs/android-termux-setup.md` — full Termux/Tasker install guide
- `docs/costco-portal-capture.md` — a real vendor portal, captured end-to-end
- `docs/lessons-learned.md` — the "why" behind the design:
  Tasker/Termux/Android gotchas, plus where every log lives
  (`mvwifi_history.log` lifecycle markers, `mvwifi_nudge.log`
  decisions, Tasker's own `runlog.txt` action results)
- `docs/architecture.md` — system design
- `docs/troubleshooting.md` — symptom→cause→fix catalog: Tasker
  import/persistence traps, plugin timeouts, binding failures,
  portal hangs — built from real field diagnoses

## License

MIT
