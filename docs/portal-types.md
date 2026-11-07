# Captive Portal Types — A Field Taxonomy

"Captive portal" covers several distinct mechanisms. Which
automation strategy applies depends entirely on which type you're
facing — this doc classifies them by what the toolkit has actually
encountered in the field.

## Detection: how you know there's a portal at all

Every OS does the same trick: fetch a tiny known-content URL and
check whether you got what you asked for.

| Probe | Expected content | Used by |
|---|---|---|
| `http://detectportal.firefox.com/canonical.html` | fixed HTML | Firefox |
| `http://connectivitycheck.gstatic.com/generate_204` | empty 204 | Android |
| `http://captive.apple.com/hotspot-detect.html` | "Success" | iOS/macOS |

If the response is a redirect (302/301) or unexpected content, a
portal interposed. **The redirect's `Location` header gives you the
portal host** — never assume it's the gateway IP; portals routinely
live on separate infrastructure (Costco's is `portal.gc1.mist.com`,
reached through a vendor CDN, not the store's gateway).

The toolkit does this in `captive_portal.detect_portal()` — probe,
follow the redirect chain, extract the host.

## Type 1: Form-acceptance portals ("check the box, click the button")

The most common public-WiFi pattern: a page with a Terms-of-Service
form — hidden session/redirect fields, usually a `tos`/`accept`
checkbox, and a submit button.

**Real example — Costco (`Costco Member Wifi`, Juniper Mist):**

```
GET  http://detectportal.firefox.com/canonical.html
→ 302 to https://portal.gc1.mist.com/logon?ap_mac=..&client_mac=..&ssid=..
GET  the logon page → HTML form with:
     hidden inputs: ap_mac, client_mac, url, wlan_id, ...
     checkbox:      tos=true
     submit:        auth_method=passphrase (misnamed — no password needed)
POST form fields + tos=true
→ 200, landing on the retailer homepage → internet verified
```

Automation: fetch the portal page, **parse the form for hidden
inputs** (don't hardcode field names — vendors rename them), add the
checkbox value, POST, then verify real internet afterward.
`captive_portal.accept_terms()` implements this pattern; the full
Costco walkthrough with real markup is in
`docs/costco-portal-capture.md`.

Sub-variants encountered — the POST always reduces to "replay the
form's fields with the accept values filled in", the differences are
only which fields exist:

| Portal | Form contents | POST body | Gotcha |
|---|---|---|---|
| **cmvwifi** | hidden `origurl` + submit button | `origurl=…&ok=Accept and Continue` | no checkbox at all — the *button label itself* is a submitted field |
| **Costco Member Wifi** | hidden Mist session fields + `tos` checkbox + submit | `ap_mac=…&client_mac=…&tos=true&auth_method=passphrase` | `auth_method=passphrase` is a red herring — no password is sent |

The generic algorithm both handlers share:

1. GET the portal page, parse the `<form>` — action URL, *every*
   hidden input (they carry session/redirect state the portal
   expects echoed back), checkbox names/values
2. POST the form fields + the accept values (checkbox checked,
   submit field included if the form carries one)
3. Re-probe the canary — see "Verification" below

Some portals hide the accept action behind a JS `onclick` rather
than a real form — the handler still extracts the form's `action` +
fields and ignores the JS entirely.

## Type 2: Credential portals ("log in with your account")

Hotel WiFi, airline WiFi, some municipal systems: the form demands
actual credentials — room number + name, loyalty account, voucher
code.

**Out of scope for this toolkit** — deliberately. Options are: store
credentials and replay (works but stores secrets in config), or log
in once manually and let the venue's MAC-remembering cover you (see
Type 4). The `portal_probe` recon harness still helps characterize
the form if you want to script one yourself.

## Type 3: Subscriber/carrier-provisioned networks

`xfinitywifi` and carrier hotspots: **open at the radio layer, but
authorization happens out-of-band** — via carrier provisioning tied
to your account/SIM, or a MAC remembered from a prior subscriber
login. No portal appears for authorized devices at all; for
unauthorized devices you get the credential portal from Type 2.

Field note: our fallback tier joins `xfinitywifi` and gets internet
immediately — the device was already authorized elsewhere (carrier
account), invisible to the automation. **Nothing to automate:**
if your account entitles the device, association is the whole story.

## Type 4: MAC-remembered portals ("accept once, gone for months")

Looks like Type 1 the *first* time; the venue's backend remembers
your device MAC afterwards and never shows the page again.

**Real example — `SHGuestNet` (healthcare guest network):** after a
single manual terms acceptance, every subsequent visit — including
different physical locations sharing the backend — returns live
internet within ~2 minutes of association. Seven consecutive
`portal_probe` captures found **no portal at all**, just open
internet.

Automation implication: build a capture profile for the SSID, run it
once, discover there's nothing to automate. This is the desired end
state — the network behaves like a home network: associate and go.

Beware: some venues expire the MAC (days to months). If the portal
ever returns, the form-acceptance path from Type 1 handles it.

## Type 5: App/deep-link portals

The portal page pushes a native app or fires deep-link intents —
common in retail/hospitality wanting engagement rather than access.
`SHGuestNet` captures showed JS assets and intent URLs even though
the *web* path was already open.

Automation: usually recon-only. If the plain form path exists,
`portal_probe` will find it; if the app is mandatory, scripting it
is a different problem (UI automation, not HTTP).

## Which type is your network? The recon workflow

1. Associate, then run `wpa-portal-probe` — it captures the redirect
   chain, portal HTML, form fields, checkboxes, JS assets, and
   deep-link intents into a timestamped directory.
2. Read the capture:
   - `internet_ok: true` already → Type 3/4, nothing to do
   - form with hidden fields + checkbox → Type 1, automatable
   - username/password inputs → Type 2, out of scope
   - app intents only → Type 5, recon-only
3. For Type 1, wire the SSID into a `WiFi Connected` Tasker profile
   calling your portal handler — see `docs/android-termux-setup.md`.

## Verification is part of the definition

A portal accept POST returning 200 does **not** mean internet works.
Always re-probe afterward (same canary URL, expect clean response)
before declaring success — several portals return a soft "accepted"
page while the MAC hasn't been authorized yet. The toolkit's
handlers treat the POST and the re-probe as one atomic operation.
