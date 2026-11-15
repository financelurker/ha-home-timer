# A local instance: screenshots and checks

The images in `docs/images/` are taken from a local Home Assistant instance (2026.11, English, light theme),
and `e2e.py` checks Home Timer on it – what the unit tests cannot: the panel as Home Assistant registers and
serves it, the real config flow and websocket API, entities, events, the to-do list and a power station.

The appliances are plugs whose power comes from `input_number` helpers (`config/configuration.yaml`), so a run
can be played back exactly. The scripts talk to `http://127.0.0.1:8123` (set `HA_URL` for another address).

1. A Python 3.14 environment with Home Assistant and Playwright, the integration linked into a copy of the
   config, then start it:

   ```bash
   python3.14 -m venv ~/hareal && ~/hareal/bin/pip install "homeassistant>=2026.11,<2026.12" playwright
   ~/hareal/bin/playwright install chromium   # or set CHROME to a Chrome binary
   cp -r config ~/htinst-config && mkdir -p ~/htinst-config/custom_components
   ln -s "$PWD/../../custom_components/home_timer" ~/htinst-config/custom_components/
   ~/hareal/bin/hass -c ~/htinst-config
   ```

2. Onboard and set up Home Timer as a user would – the first area through the config flow, the rest through
   the panel's websocket API (writes `session.json` with a token; git ignores it). It needs a fresh config folder
   (onboarding runs once):

   ```bash
   ~/hareal/bin/python setup_demo.py
   ```

3. Check Home Timer on the instance (exit code 1 on a failure). It uses its own plug and puts back what it
   changes. Its runs show in Hob 1's activity log, so for clean screenshots run step 4 before it:

   ```bash
   ~/hareal/bin/python e2e.py
   ```

4. Set the scene (timers running, the kettle ringing, the moka pot brewing, the dashboard *Timers*) and take the
   screenshots. `shoot.py` also checks the ways into the panel in the real frontend (the cog of an area, a
   device's link):

   ```bash
   ~/hareal/bin/python scene.py
   ~/hareal/bin/python shoot.py ../../docs/images            # all, plus the browser checks
   ~/hareal/bin/python shoot.py ../../docs/images panel,phone # some
   ```

5. Update the image versions in the README (its image URLs carry a hash of the file, so a changed image is not
   shown from a cache; a test fails while they are out of date):

   ```bash
   python ../readme_images.py
   ```
