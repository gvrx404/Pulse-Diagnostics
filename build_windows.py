#!/usr/bin/env python3
"""Windows build: React + embedded Chromium + hardware DLLs -> one EXE.

Run: py -3 build_windows.py   or double-click build_release.bat
The build machine needs Python 3.11+ (x64), Internet, .NET Framework 4.8.
Node.js is needed only if you choose to rebuild the React UI.
The end user does NOT need LibreHardwareMonitor.exe or a Python installation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / 'vendor'
OUT = VENDOR / 'lhm'
PACKAGES = VENDOR / '_nuget'
NUGET = VENDOR / '_tools' / 'nuget.exe'
PACKAGE_NAME = 'LibreHardwareMonitorLib'
PACKAGE_VERSION = '0.9.6'  # stable version, not a nightly/pre-release
PAWNIO_VERSION = '2.2.0'
PAWNIO_URL = ('https://github.com/namazso/PawnIO.Setup/releases/download/'
              + PAWNIO_VERSION + '/PawnIO_setup.exe')
PAWNIO_SHA256 = '1f519a22e47187f70a1379a48ca604981c4fcf694f4e65b734aaa74a9fba3032'
PAWNIO_DIR = VENDOR / 'pawnio'
PAWNIO_FILE = PAWNIO_DIR / 'PawnIO_setup.exe'



def run(argv, cwd=ROOT):
    print('\n>', subprocess.list2cmdline([str(x) for x in argv]), flush=True)
    subprocess.run([str(x) for x in argv], cwd=cwd, check=True)


def download(url, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    print('Downloading:', url, flush=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'LaptopDiag-Builder/2.0'})
    with urllib.request.urlopen(req, timeout=75) as r, dest.open('wb') as f:
        shutil.copyfileobj(r, f)
    if dest.stat().st_size < 100_000:
        raise RuntimeError('Downloaded file is unexpectedly small: ' + str(dest))


# Keep this list ordered for the classic .NET Framework CLR loaded by pythonnet.
# Never copy reference assemblies from ref/: they cannot run.
FRAMEWORKS = ('net472', 'net48', 'net471', 'net47', 'net462', 'net461',
              'net46', 'net452', 'net45', 'net40', 'netstandard2.0')
ASSEMBLY_ROOTS = ('runtimes/win-x64/lib', 'runtimes/win/lib', 'lib')
NATIVE_ROOTS = ('runtimes/win-x64/native', 'runtimes/win/native')


def select_assembly(pkg):
    """Find compatible *implementation* assemblies for 64-bit .NET Framework.

    LibreHardwareMonitorLib 0.9.6 uses runtime-specific NuGet paths such as
      runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll
    instead of the conventional lib/net472 path. A package's ref/ folder
    contains non-runnable reference assemblies and must not be selected.
    """
    for framework in FRAMEWORKS:
        for rel_root in ASSEMBLY_ROOTS:
            directory = pkg / rel_root / framework
            if directory.is_dir():
                dlls = sorted(p for p in directory.glob('*.dll') if p.is_file())
                if dlls:
                    return dlls
    return []


def prepare_vendor():
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = OUT / 'BUILD_MANIFEST.json'
    if stamp.is_file() and (OUT / 'LibreHardwareMonitorLib.dll').is_file():
        try:
            manifest = json.loads(stamp.read_text(encoding='utf-8'))
            if (manifest.get('version') == PACKAGE_VERSION and
                    manifest.get('framework') == 'net472-win-x64' and
                    manifest.get('files') and
                    all((OUT / filename).is_file() for filename in manifest['files'])):
                print('LibreHardwareMonitor NuGet runtime already staged:', OUT)
                return
        except (OSError, ValueError, TypeError):
            pass
    if not NUGET.is_file():
        download('https://dist.nuget.org/win-x86-commandline/latest/nuget.exe', NUGET)
    # Downloads missing packages; already-downloaded ones are reused by NuGet.
    run([NUGET, 'install', PACKAGE_NAME, '-Version', PACKAGE_VERSION,
         '-Framework', 'net472', '-DependencyVersion', 'Lowest',
         '-OutputDirectory', PACKAGES,
         '-Source', 'https://api.nuget.org/v3/index.json', '-NonInteractive'])

    # Validate the real x64 implementation before collecting dependencies.
    # In particular, the NuGet ref/ DLL is NOT a usable substitute.
    main_pkg = PACKAGES / (PACKAGE_NAME + '.' + PACKAGE_VERSION)
    if not main_pkg.is_dir():
        raise RuntimeError('NuGet installation missing ' + str(main_pkg))
    primary_candidates = select_assembly(main_pkg)
    primary = next((p for p in primary_candidates
                    if p.name.lower() == 'librehardwaremonitorlib.dll'), None)
    if primary is None:
        installed = [str(p.relative_to(main_pkg)) for p in
                     main_pkg.rglob('LibreHardwareMonitorLib.dll')]
        raise RuntimeError('No usable x64 LibreHardwareMonitor runtime DLL found. '
                           'DLL paths in package: ' + (', '.join(installed) or 'none'))
    print('Using official x64 LHM runtime:', primary.relative_to(main_pkg), flush=True)

    # Clear stale staging from failed/older builds, but leave the downloaded
    # NuGet packages alone (so rerunning a failed build is inexpensive).
    for file in OUT.iterdir():
        if file.is_file() and file.suffix.lower() in ('.dll', '.sys'):
            file.unlink()

    staged = {}
    def stage(file):
        target = OUT / file.name
        if target.name.lower() in staged:
            earlier = staged[target.name.lower()]
            if hashlib.sha256(earlier.read_bytes()).digest() != hashlib.sha256(file.read_bytes()).digest():
                raise RuntimeError('Conflicting DLL versions: ' + str(earlier) + ' vs ' + str(file))
            return
        shutil.copy2(file, target)
        staged[target.name.lower()] = file

    # Stage the primary package first; other packages may have both runtime-
    # specific and normal lib/ implementations.
    stage(primary)
    for pkg in sorted(PACKAGES.iterdir()):
        if not pkg.is_dir() or pkg == main_pkg:
            continue
        for file in select_assembly(pkg):
            stage(file)
        # Native Windows dependencies are different from managed lib DLLs.
        for rel_root in NATIVE_ROOTS:
            native_dir = pkg / rel_root
            if native_dir.is_dir():
                for file in sorted(native_dir.iterdir()):
                    if file.is_file() and file.suffix.lower() in ('.dll', '.sys'):
                        stage(file)
    for rel_root in NATIVE_ROOTS:
        native_dir = main_pkg / rel_root
        if native_dir.is_dir():
            for file in sorted(native_dir.iterdir()):
                if file.is_file() and file.suffix.lower() in ('.dll', '.sys'):
                    stage(file)

    if not (OUT / 'LibreHardwareMonitorLib.dll').is_file():
        raise RuntimeError('Failed to stage LibreHardwareMonitorLib.dll')
    print('Staged', len(staged), 'managed/native assemblies into', OUT)
    print('LibreHardwareMonitor library SHA256:',
          hashlib.sha256((OUT / 'LibreHardwareMonitorLib.dll').read_bytes()).hexdigest())
    (OUT / 'THIRD_PARTY_NOTICE.txt').write_text(
        'LibreHardwareMonitorLib version ' + PACKAGE_VERSION + '\n'
        'Copyright LibreHardwareMonitor contributors. MPL-2.0 license.\n'
        'Source: https://github.com/LibreHardwareMonitor/LibreHardwareMonitor\n'
        'NuGet package: https://www.nuget.org/packages/LibreHardwareMonitorLib/' + PACKAGE_VERSION + '\n'
        'Dependencies are distributed under their respective licenses.\n'
        'Full package licenses and metadata available in vendor/_nuget after installation.\n',
        encoding='utf-8')
    stamp.write_text(json.dumps({'version': PACKAGE_VERSION,
                                 'framework': 'net472-win-x64',
                                 'primary_path': str(primary.relative_to(main_pkg)).replace('\\', '/'),
                                 'files': sorted(p.name for p in OUT.iterdir()
                                                 if p.is_file() and p.suffix.lower() in ('.dll', '.sys'))},
                                indent=2), encoding='utf-8')


def prepare_pawnio():
    """Bundle the official, SHA256-pinned driver installer inside the single EXE.

    Installing a kernel driver is separate and requires an explicit user click
    after launching Pulse. Building the app never installs this driver.
    """
    PAWNIO_DIR.mkdir(parents=True, exist_ok=True)
    if PAWNIO_FILE.is_file():
        actual = hashlib.sha256(PAWNIO_FILE.read_bytes()).hexdigest()
        if actual == PAWNIO_SHA256:
            print('Using verified PawnIO 2.2.0 installer:', PAWNIO_FILE)
            return
        PAWNIO_FILE.unlink()
    download(PAWNIO_URL, PAWNIO_FILE)
    actual = hashlib.sha256(PAWNIO_FILE.read_bytes()).hexdigest()
    if actual != PAWNIO_SHA256:
        PAWNIO_FILE.unlink(missing_ok=True)
        raise RuntimeError('PawnIO downloaded file failed SHA256 verification. Aborting build.')
    if PAWNIO_FILE.read_bytes()[:2] != b'MZ':
        raise RuntimeError('PawnIO installer is not a Windows executable.')
    (PAWNIO_DIR / 'SOURCE.txt').write_text(
        'Official PawnIO 2.2.0 installer\n' + PAWNIO_URL + '\nSHA256: ' + PAWNIO_SHA256 +
        '\nThe user must explicitly click Install hardware access before installation.\n',
        encoding='utf-8')
    print('Verified PawnIO 2.2.0 (will only install after explicit user consent).')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--skip-ui', action='store_true', help='reuse a previously compiled React UI')
    p.add_argument('--no-admin', action='store_true', help='do not request elevated permissions at app startup')
    p.add_argument('--console', action='store_true', help='show a console for Windows crash diagnostics')
    p.add_argument('--onedir', action='store_true', help='build a folder version to reduce onefile bootloader / AV issues')
    a = p.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Windows EXE builds must run on Windows 10/11, not Linux/macOS.')
    if sys.maxsize <= 2**32:
        raise SystemExit('Use 64-bit Python (x64) to build this app.')
    if not a.skip_ui:
        run([sys.executable, ROOT / 'frontend' / 'build.py'])
    prepare_vendor()
    prepare_pawnio()
    run([sys.executable, '-m', 'pip', 'install', '-r', ROOT / 'requirements.txt'])
    # QtWebEngine is shipped inside PySide6 6.11.x; 6.12+ split its wheels.
    # Import check prevents accidentally shipping a WebEngine-less binary.
    run([sys.executable, '-c', 'from PySide6.QtWebEngineWidgets import QWebEngineView; from PySide6.QtWebChannel import QWebChannel; print("Embedded Chromium QtWebEngine detected")'])
    run([sys.executable, '-m', 'pip', 'install', 'pyinstaller>=6.22,<7'])
    if not (ROOT / 'ui' / 'index.html').is_file():
        raise RuntimeError('Missing built React UI. Run python frontend/build.py')
    args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
            '--onedir' if a.onedir else '--onefile',
            '--console' if a.console else '--windowed', '--name', 'PulseDiagnostics',
            '--distpath', ROOT / 'dist', '--workpath', ROOT / '.build',
            '--specpath', ROOT, '--collect-all', 'pythonnet',
            '--collect-all', 'clr_loader', '--hidden-import', 'clr',
            '--hidden-import', 'hardware_sensors', '--hidden-import', 'assessment',
            '--hidden-import', 'optimizer', '--hidden-import', 'winreg',
            '--hidden-import', 'chromium_shell',
            '--hidden-import', 'PySide6.QtWebEngineWidgets',
            '--hidden-import', 'PySide6.QtWebEngineCore',
            '--hidden-import', 'PySide6.QtWebChannel',
            '--add-data', str(ROOT / 'ui') + ';ui',
            '--add-data', str(OUT) + ';vendor/lhm',
            '--add-data', str(PAWNIO_DIR) + ';vendor/pawnio',
            '--exclude-module', 'webview', '--exclude-module', 'cefpython3']
    icon = ROOT / 'assets' / 'pulse.ico'
    if icon.is_file():
        args += ['--icon', str(icon), '--add-data', str(ROOT / 'assets') + ';assets']
    if not a.no_admin:
        args.append('--uac-admin')
    args.append(ROOT / 'main.py')
    run(args)
    exe = ROOT / 'dist' / ('PulseDiagnostics/PulseDiagnostics.exe' if a.onedir else 'PulseDiagnostics.exe')
    if not exe.is_file():
        raise RuntimeError('PyInstaller did not produce an EXE')
    if a.onedir:
        helpers = list((ROOT / 'dist' / 'PulseDiagnostics').rglob('QtWebEngineProcess.exe'))
        if not helpers:
            raise RuntimeError('QtWebEngineProcess.exe missing from onedir build. Chromium cannot launch.')
        print('Verified embedded Chromium helper:', helpers[0])
    print('\nSUCCESS! Chromium packaged EXE:', exe, '\nSize:', round(exe.stat().st_size / 1048576, 1), 'MB')
    print('No separate LibreHardwareMonitor application is needed.')
    print('GUI renderer: bundled Chromium via Qt WebEngine; no Edge WebView2 runtime or browser needed.')
    if a.no_admin:
        print('Standard-user build (recommended); administrator-only operations require explicit elevation.')
    print('Startup log: %LOCALAPPDATA%\\PulseDiagnostics\\startup.log')
    if a.onedir:
        print('Distribute the WHOLE dist\\PulseDiagnostics folder, not only its EXE.')
    print('After launch, click Install hardware access if CPU sensors are missing.')


if __name__ == '__main__':
    main()
