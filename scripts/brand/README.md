# Brand icon

`icon.svg` is the source of the icon in `custom_components/home_timer/brand/`
(`icon.png` 256×256, `icon@2x.png` and `logo.png` 512×512).

- Home Assistant 2026.3 and newer shows it on the integration and device pages.
- HACS reads `custom_components/home_timer/brand/icon.png` from the repository
  (at the release tag, otherwise the default branch) and, once downloaded, from the
  installed copy. The release ZIP contains the `brand/` folder as well.

## Origin

The icon was drawn from scratch for this project, as hand-written SVG from basic shapes:
a house-shaped icon with a turquoise gradient and, on it, a white house outline that runs
down like a timer (the part already used is faint) and two clock hands. No template, icon
set, stock image or other third-party artwork was used. House outline and clock hands are
generic symbols. It was chosen in two design rounds of 12 drafts each.

## Changing it

Edit `icon.svg`, then render the PNGs (Playwright with Chromium) and commit both:

```bash
cd scripts/brand
node render.js
```

The source stays outside the integration folder, so it is not part of the release ZIP.
