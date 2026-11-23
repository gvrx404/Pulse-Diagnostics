# Changelog

## Unreleased — Website

- Added a standalone responsive product landing page with interactive theme screenshots.
- Added an opt-in GitHub Pages deployment workflow.
- Documented local preview and GitHub Pages publishing.

## Repository preparation — 2026-11-23

- Renamed the main Python modules and Windows helper scripts for clearer purpose-based naming.
- Updated imports, tests, builder entry points and frontend paths to match.
- Added a GitHub-ready README, build guide, third-party documentation and `.gitignore`.
- Removed the checked-in NuGet cache, bundled vendor binaries and machine-specific PyInstaller spec from the source distribution. The Windows builder fetches and verifies dependencies automatically.
- Preserved the compiled offline React interface and existing application functionality.

This entry describes the repository cleanup, not a new hardware feature release.
