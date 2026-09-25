# G8 manual checklist: ValhallISC on macOS

Test server: host `localhost`, port `52773`, user `_SYSTEM`, password `irisfs-test` (the Docker IRIS container).

| # | Step | Expected | Result |
|---|---|---|---|
| 1 | Launch the app | The valknut icon appears in the menu bar (dimmed = nothing mounted); no Dock icon. The first run opens Profiles… | |
| 2 | Profiles: **+**, fill in the test server, mount folder `~/ValhallISC/Local`, **Test connection** | "Connection OK" with the namespaces listed | |
| 3 | **Save** | The profile appears in the list with ○ | |
| 4 | Click the menu bar icon → click the profile | The profile shows "(connecting…)", then ✓. The icon becomes solid. The folder shows in Finder with the namespaces | |
| 5 | With the profile mounted, open Profiles… | The form is read-only, the trash button is disabled, and a note says "Mounted — unmount it…" | |
| 6 | Click the mounted profile in the menu → **Unmount** | The ✓ disappears and the folder is empty again | |
| 7 | Profiles: change the password to a wrong one, Save, mount | A clear "rejected the user name or password" error; nothing mounted | |
| 8 | Profiles: delete a profile (trash) | A confirmation, then it's removed | |
| 9 | Mount 2 profiles (e.g. a second read-only one on another folder), then **Quit** | The confirmation lists both. Both are unmounted and the app exits | |
| 10 | Mount, `cd` into the folder in Terminal, then Quit | A "Folders in use" prompt appears; **Force unmount** works | |
| 11 | Dark and light menu bar (wallpaper or appearance) | The icon is visible in both | |
| 12 | Drag an .xml into a mounted namespace | A notification "…: imported" appears (best effort) | |
