# G8 manual checklist: ValhallISC on macOS

Test server: host `localhost`, port `52773`, user `_SYSTEM`, password `irisfs-test` (the Docker IRIS container).

| # | Step | Expected | Result |
|---|---|---|---|
| 1 | Launch the app | The valknut icon appears in the menu bar (dimmed = nothing mounted); no Dock icon. The first run opens Profiles… | |
| 2 | Profiles: **+** (under the list), fill in the test server, mount folder `~/ValhallISC/Local`, **Test connection** | an inline ✓ "Connected to IRIS … Namespaces: …" next to the button | |
| 3 | **Save** | The profile appears in the list: grey dot, "localhost:52773 · not mounted" | |
| 4 | Click the menu bar icon → click the profile | "Local — Connecting…", then "Local — Mounted ▸"; the header says "1 of 1 mounted"; the icon becomes solid; the folder shows in Finder with the namespaces | |
| 5 | With the profile mounted, open Profiles… | green dot + "Mounted · ~/ValhallISC/Local"; a green banner with **Open folder** (opens Finder) and **Unmount…**; the form is read-only; trash disabled | |
| 6 | Menu → "Local — Mounted ▸" → **Unmount…** → confirm | the entry goes back to "Local"; the folder is empty again | |
| 7 | Profiles: change the password to a wrong one, Save, mount | A clear "rejected the user name or password" error; nothing mounted | |
| 8 | Profiles: delete a profile (trash) | A confirmation, then it's removed | |
| 9 | Mount 2 profiles (e.g. a second read-only one on another folder), then **Quit** | The confirmation lists both. Both are unmounted and the app exits | |
| 10 | Mount, `cd` into the folder in Terminal, then Quit | A "Folders in use" prompt appears; **Force unmount** works | |
| 11 | Dark and light menu bar (wallpaper or appearance) | The icon is visible in both | |
| 12 | Drag an .xml into a mounted namespace | A notification "…: imported" appears (best effort) | |
