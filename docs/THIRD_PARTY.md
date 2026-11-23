# Third-Party Components

Pulse Diagnostics uses independently developed components. The following are upstream references, **not** a substitute for the full applicable license and notices.

| Component | Purpose | Upstream |
| --- | --- | --- |
| PySide6 / Qt WebEngine | Native desktop shell and Chromium renderer | https://www.qt.io/qt-for-python |
| React / ReactDOM | Dashboard interface | https://react.dev |
| esbuild | Bundles the frontend | https://esbuild.github.io |
| psutil | OS metrics | https://github.com/giampaolo/psutil |
| pythonnet | Python ↔ .NET interop | https://github.com/pythonnet/pythonnet |
| LibreHardwareMonitorLib | Embedded hardware sensors | https://github.com/LibreHardwareMonitor/LibreHardwareMonitor |
| PawnIO (optional) | Low-level hardware-access driver | https://github.com/namazso/PawnIO.Setup |
| PyInstaller | Builds Windows desktop distributions | https://pyinstaller.org |

`build_windows.py` acquires **LibreHardwareMonitorLib 0.9.6** using NuGet and **PawnIO setup 2.2.0** from the linked upstream release with SHA-256 verification. Other .NET dependencies are selected and staged from their respective NuGet packages.

Before publishing a binary or releasing an open-source repository:

1. Review the **license of each dependency**, including Qt/PySide6 and all transitive .NET packages.
2. Retain required notices, attribution, license texts and any applicable source/redistribution obligations.
3. Confirm that distribution of bundled drivers/installers complies with their own terms.
4. Add a project-wide license of your choosing for **your own** source only after deciding what permissions to grant. No such license was provided with this upload.

NuGet package caches and binary installers are excluded from the GitHub source release; the Windows builder retrieves them on demand.
