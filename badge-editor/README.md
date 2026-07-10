# Badge Editor

Edit your badge's `secrets.py` from a browser, delivered over the air.

## How it works

```
Browser ──edit/save──▶ Next.js server (this app) ◀──register/poll── Badge "Editor" app
```

1. The **Editor** badge app (`badge/apps/editor/`) boots, derives a unique id
   that survives power cycles (the RP2350's factory chip id on hardware, a
   persisted random id in the simulator), connects to WiFi, and registers
   itself — uploading its current `secrets.py`.
2. The badge fetches a QR code from this server and draws it on screen. The
   QR encodes `http://<your-computer's-LAN-IP>:3000/b/<badge-id>`.
3. Scan the QR with your phone → the settings page opens → edit `secrets.py`
   → **Save & push**.
4. The badge polls every 3 seconds, sees the new version, writes it to flash,
   and confirms. Both the badge screen and the web page then prompt you to
   press **RESET** on the badge to load the new settings.

## Running

```bash
cd badge-editor
npm install
npm run dev        # or: npm run build && npm start
```

Then start the badge app:

- **Simulator** (same machine, works out of the box):
  `python simulator/badge_simulator.py badge/apps/editor`
- **Real hardware**: set `EDITOR_SERVER = "http://<your-LAN-IP>:3000"` in the
  badge's `secrets.py` (see the Editor Settings section there), copy
  `badge/apps/editor/` to `/system/apps/editor/` on the badge, and launch
  the Editor app. Phone and badge must be on the same network as this server.

The landing page at [http://localhost:3000](http://localhost:3000) lists every
badge that has registered, so you can also click through without scanning.

## Notes

- State lives in `.data/badges.json` (gitignored); delete it to forget badges.
- Everything stays on your LAN — secrets are never sent anywhere else.
- The server binds `0.0.0.0` so phones on the network can reach it.
