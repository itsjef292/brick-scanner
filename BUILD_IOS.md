# Building the native iOS app (Capacitor shell)

The native iOS app is a thin **Capacitor** shell that loads the live Brick Scanner
web app from your Mac over Tailscale (`server.url`) and adds an **on-device ML Kit
Data Matrix scanner** for the CMF tab — far stronger than the in-browser/server
decode (this is the WhatFig-grade scanning).

**Nothing about the web app changes.** The browser version (over Tailscale or
Render) keeps its existing camera + libdmtx flow untouched; the native scanner
code is feature-detected and only runs inside the iOS app.

> **Where to build:** *not* on the headless Mac mini server — on a Mac with
> **Xcode** installed, with your iPhone available. The committed scaffold lives in
> `native/`; the `ios/` Xcode project and `node_modules/` are generated there.

---

## One-time prerequisites (on the build Mac)

```bash
# 1. Xcode — install from the App Store, then accept the license + install components:
sudo xcodebuild -license accept
xcodebuild -runFirstLaunch

# 2. Node.js (LTS) — e.g. via Homebrew:
brew install node

# 3. CocoaPods (Capacitor uses it to pull the ML Kit pods):
brew install cocoapods
```

You also need an **Apple ID** for signing. Free works, but the app expires after
**7 days** (just re-run from Xcode to reinstall). An **Apple Developer account
($99/yr)** gives a 1-year signing + TestFlight if you ever want it.

---

## First build

```bash
cd native
npm install                 # pulls Capacitor + the ML Kit barcode plugin
npx cap add ios             # generates native/ios/ (the Xcode project)
npx cap sync ios            # copies config + runs `pod install` for the plugins
npx cap open ios            # opens the project in Xcode
```

In **Xcode**:

1. Select the **App** target → **Signing & Capabilities** → check *Automatically
   manage signing* and pick your **Team** (your Apple ID). Change the **Bundle
   Identifier** if Xcode complains it's taken (e.g. `com.itsjef.brickscanner2`).
2. **Add the camera usage string** (required, or the app crashes when scanning):
   open `App/App/Info.plist` and add a row
   `Privacy - Camera Usage Description` = `Scan LEGO CMF box codes`.
   (Key: `NSCameraUsageDescription`.)
3. Plug in your iPhone, pick it in the device dropdown, press **▶ Run**.
4. First launch on the phone: **Settings → General → VPN & Device Management →**
   trust your developer certificate.

The app opens to the live web app. Make sure:
- your Mac mini is running the Flask app (the `com.brickscanner.app` launchd agent), and
- your iPhone is on the **Tailscale** network (the app loads `https://jefs-mac-mini.tailbdd458.ts.net`).

Go to the **CMF** tab → tap **Scan CMF Box** → the native scanner opens → it reads
the Data Matrix and drops you into the normal lookup / tag / add flow.

---

## Day-to-day

- **Changing the web app** (Flask, `templates/index.html`, etc.): just change it —
  the native app loads the live site, so it updates on next launch. **No rebuild.**
- **Changing native config/plugins** (`capacitor.config.json`, adding a plugin):
  `cd native && npx cap sync ios`, then re-run in Xcode.
- **Different Mac / new Tailscale hostname:** edit `server.url` in
  `native/capacitor.config.json`, then `npx cap sync ios`.

---

## Notes & fallback

- The scanner uses **`@capacitor-mlkit/barcode-scanning`** with the `DATA_MATRIX`
  format. It decodes the box's Data Matrix and hands the raw payload to the web
  app, which extracts the first 7-digit field (`_cmf7`) — see
  `cmf-datamatrix-payload-structure` notes.
- The integration calls `BarcodeScanner.scan({ formats: ['DATA_MATRIX'] })`
  (single-shot). If that method doesn't present a scanner on your iOS build, the
  alternative is the continuous **`startScan`/`stopScan`** mode with a transparent
  WebView (keeps the immersive in-app overlay). Ping me and I'll switch it — it's
  ~30 lines and a bit of CSS.
- `ios/` and `node_modules/` are git-ignored; only the `native/` scaffold
  (`package.json`, `capacitor.config.json`, `www/`) is committed, so a fresh
  checkout rebuilds with the steps above.
