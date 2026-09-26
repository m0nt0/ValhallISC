# Third-party components

ValhallISC is licensed under the GNU General Public License v3.0 or later (see `LICENSE`). The packaged applications bundle the components below. All of them have licenses compatible with distribution under the GPL v3.

| Component | Version (at the time of writing) | License |
|---|---|---|
| Python (CPython runtime) | 3.12 | PSF License |
| wxPython / wxWidgets | 4.3.1 / 3.3.3 | wxWindows Library Licence (LGPL-2.0 with a binary exception) |
| httpx, httpcore | 0.28.1, 1.0.9 | BSD-3-Clause |
| anyio, sniffio, h11, idna, certifi | 4.15, –, 0.16, 3.20, 2026.7 | MIT, MIT/Apache-2.0, MIT, BSD-3-Clause, MPL-2.0 |
| defusedxml | 0.7.1 | PSF License |
| mfusepy | 3.1.1 | ISC |
| platformdirs | 4.11 | MIT |
| keyring, jaraco.*, more-itertools | 25.7, … | MIT |
| pyobjc-core, pyobjc-framework-Cocoa (macOS only) | 12.2 | MIT |
| GNU libiconv, gettext libintl (macOS build, from the Python runtime) | 1.17 / 0.22 | LGPL-2.1-or-later |
| PyInstaller bootloader | 6.22 | GPL-2.0-or-later with the bootloader exception |

**Not bundled:** the FUSE drivers (macFUSE, FUSE-T, WinFsp, libfuse3). The user installs them separately under their own licenses.

InterSystems and IRIS are trademarks of InterSystems Corporation. ValhallISC is not affiliated with, or endorsed by, InterSystems.
