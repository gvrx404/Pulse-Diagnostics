# Building and Running Pulse Diagnostics

## Requirements

- **Windows 10 or Windows 11**, 64-bit.
- **64-bit Python 3.11+** with `pip` available.
- **.NET Framework 4.8** for the embedded LibreHardwareMonitor library via pythonnet.
- Internet access for the **first build** (Python dependencies, the NuGet CLI, LibreHardwareMonitor assemblies and verified optional PawnIO installer).
- **Node.js / npm only when rebuilding frontend sources.** A precompiled UI is already present at `ui/index.html`.

The Qt WebEngine (Chromium) runtime is bundled by PyInstaller. **WebView2 is not needed** for the packaged application.

## Release EXE

Run from the repository root:

```bat
build_release.bat
```

Equivalent CLI:

```powershell
py -3 build_windows.py --skip-ui --no-admin
```

Result: `dist\PulseDiagnostics.exe` (single executable that extracts the Qt/Chromium resources at launch).

The script installs dependencies, stages official LibreHardwareMonitor NuGet runtime DLLs and verifies the PawnIO setup SHA-256 before packaging. The driver setup is **bundled, not automatically installed**.

## Debug / Folder Build

```bat
build_debug.bat
```

Equivalent CLI:

```powershell
py -3 build_windows.py --skip-ui --no-admin --onedir --console
```

Output: `dist\PulseDiagnostics\PulseDiagnostics.exe`. Keep the folder and its `_internal` files together. This form is easier to inspect if antivirus software blocks Qt WebEngine subprocesses.

## Build Options

| Flag | Meaning |
| --- | --- |
| `--skip-ui` | Use the already-built `ui/index.html` instead of running npm/esbuild. |
| `--no-admin` | Don't request an elevated token automatically when starting the packaged app. |
| `--console` | Show diagnostic stdout/stderr. |
| `--onedir` | Produce a folder build rather than single-executable packaging. |

Without `--skip-ui`, the builder executes `frontend/build.py`, which installs the specified React/esbuild npm dependencies and generates the offline `ui/index.html`.

## Frontend Changes

From the repository root:

```powershell
python frontend/build.py
python build_windows.py --skip-ui --no-admin
```

The frontend uses local React, JavaScript and CSS assets and is loaded through Qt WebEngine. The Qt WebChannel bridge lives in `frontend/qt_bridge.js` and `chromium_shell.py`.

## Tests

```powershell
python -m unittest discover -s tests -v
```

`run_tests.bat` provides the same command on Windows. The tests use fake/mocked operating-system services where appropriate and **do not** prove that your specific hardware sensors are readable.

## Known Limitations

- Sensor readings depend on manufacturer firmware, motherboard access, available drivers and privileges. Missing measurements are reported as unavailable.
- Elevated operations may modify system settings. Use these only with permission and make sure you understand each action.
- Bundled Chromium makes EXE output significantly larger and may increase one-file startup time due to extraction.
- Building on Linux/macOS does not create a functional Windows EXE; build and verify it on Windows.

## Troubleshooting

**Application fails to launch:** run the debug folder build; inspect `%LOCALAPPDATA%\PulseDiagnostics\startup.log`, check QtWebEngineProcess.exe and related DLLs, and verify no antivirus quarantine occurred. Do not install Edge WebView2 to repair the bundled Qt Chromium renderer.

**Dependencies fail to install:** verify x64 Python, network access to PyPI, the NuGet feed and the upstream PawnIO release. `vendor/_nuget` and `vendor/_tools` caches are generated locally and need not be checked into Git.

**Hardware information is incomplete:** check sensor availability and source labels in the Hardware Sensors page. A valid GPU reading does not imply that CPU temperature or package power is available.

**Frontend appears unchanged:** regenerate `ui/index.html` with `python frontend/build.py`, then rebuild the executable. `--skip-ui` intentionally reuses the current file without recompilation.
