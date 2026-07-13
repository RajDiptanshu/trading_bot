# Remote access to the cockpit (from any device)

The cockpit runs on your Windows PC at `http://127.0.0.1:8650`. To reach it from your
phone/laptop **anywhere**, use **Tailscale** — a private VPN mesh where only devices
signed into *your own* account can connect.

> **Why not ngrok / a public tunnel?** The app has **no login of its own**. A public URL
> would let anyone who finds it read your book, trigger paper trades, and burn Claude API
> credits via the chat analyst. Tailscale keeps it private to your devices only.

## One-time setup

**On this PC** (needs an admin prompt + a browser sign-in — do these yourself):
```
winget install tailscale.tailscale     # approve the Windows admin (UAC) prompt
tailscale up                            # a browser opens — sign in (Google/GitHub/email)
```

**On each other device** (phone, laptop): install the **Tailscale** app from its app store
and sign in with the **same account**.

**Enable HTTPS once** (so the private URL works): open
<https://login.tailscale.com/admin/dns> and turn ON **MagicDNS** and **HTTPS Certificates**.

## Every time you want remote access

1. Start the cockpit: double-click **`start.bat`** (as usual).
2. Double-click **`remote_access.bat`** — it publishes the app to your tailnet and prints
   your private URL, e.g. `https://<your-pc-name>.<tailnet>.ts.net`.
3. Open that URL on any signed-in device. Done.

`tailscale serve` persists across reboots, so after the first run you normally just need
the cockpit running. To turn remote access **off**: `tailscale serve reset`.

## Notes
- The app stays bound to `127.0.0.1` (loopback). Tailscale proxies to it — nothing is
  exposed on your local WiFi or the public internet.
- Fallback (only if you skip the HTTPS step): set `APP_HOST=0.0.0.0` before launching
  `app.py`, then browse to `http://<your-tailscale-100.x.y.z-IP>:8650`. Less clean and it
  also listens on your LAN, so prefer `tailscale serve` above.
- Free Tailscale (Personal plan) is enough: up to 100 devices.
