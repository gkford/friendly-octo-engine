# 8BitDo Pro 3 + iPad — controller dropout checklist

The game's logs show the Safari page is constantly losing and regaining focus
during play (`page BLUR` / `page focus` in `./infra/logs.sh --live`). Safari
stops delivering gamepad input to an unfocused page, so every blur is a
controller dropout. The game now auto-pauses and auto-resumes around these,
but the goal is to stop them happening.

Facts confirmed from the official Pro 3 manual
(https://download.8bitdo.com/Manual/Controller/Pro3/8BItDo-Pro-3-Bluetooth-Gamepad-for-EN.pdf):

- **D mode is the correct mode for Apple/iPad** — there is no Apple-specific
  "A" mode on the Pro 3. If the mode switch (back of the controller) is on D,
  the mode is right; leave it alone.
- The Pro 3 has **no share/capture button**. Its special buttons are: Home,
  star, profile, select/start, L4/R4, and the PL/PR back paddles.

## Things to check, in order

### 1. iPad Accessibility settings (most likely, 3 minutes)
Any of these make controller/external-device buttons double as *system*
controls, which steals focus from Safari on every press:

- **Settings → Accessibility → Touch → AssistiveTouch** → should be **OFF**
- **Settings → Accessibility → Switch Control** → should be **OFF**
  (also open *Switches* inside it and confirm nothing is bound)
- **Settings → Accessibility → Keyboards & Typing → Full Keyboard Access**
  → should be **OFF**

### 2. Back paddles PL/PR and L4/R4 (likely, 5 minutes)
The paddles are mappable to any button and are easy to squeeze *rhythmically
without noticing* while gripping the controller — which matches the constant
focus-flapping in the logs. If one is mapped to Home (or anything the iPad
treats as a system button), that's the bug.

- To clear a paddle's mapping (manual p.07): hold the paddle, press **star**
  → paddle = no action. E.g. `PL + ★` clears PL.
- Test: play a round deliberately not touching the paddles and watch
  `./infra/logs.sh --live` — if the BLUR lines stop, that was it.

### 3. Accidental turbo (quick to check)
Turbo is armed by easy-to-hit combos (manual p.06): `A + ★` = turbo,
`A + ★ ★` = auto-turbo (fires repeatedly on its own). Turn off:
`A + ★` again (LED indicates state). If weird rapid inputs ever appear,
this is why.

### 4. Firmware update (10 minutes)
Get **Ultimate Software V2** from https://app.8bitdo.com (iPad app or
desktop). Connect the Pro 3, apply any offered firmware update — early Pro 3
firmware had Bluetooth quirks on Apple devices. While in the app you can also
see/clear the paddle mappings from #2, and remap the Home button away if it
turns out to be the culprit.

## How to confirm a fix

Run `./infra/logs.sh --live` in the codespace while someone plays.

- Fixed: the `page BLUR` lines stop appearing during play.
- Still broken: each `page BLUR` line now records which controller buttons
  were pressed just before it (e.g. `page BLUR after buttons [16] just now` —
  16 = Home, 17+ = paddles/extras, 4/5 = L1/R1). That tells us exactly which
  button is stealing focus; bring that to Claude and we remap or disable it.
