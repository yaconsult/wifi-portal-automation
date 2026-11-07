"""Back-compat shim — the implementation moved to ``portal_probe``.

The deployed ``costco_probe`` Termux wrapper calls
``python -m wifi_portal_automation.costco_probe``; keep that path working with
Costco's defaults (name=costco, package=com.costco.app.android —
both are portal_probe defaults anyway).
"""

import sys

from wifi_portal_automation.portal_probe import main

if __name__ == "__main__":
    sys.exit(main())
