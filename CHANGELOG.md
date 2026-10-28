# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] - 2026-10-28

### Fixed

- Fix the Configure flow crashing while building device options.
- Show selected device names on the MAC confirmation and edit screens.
- Name the device that already owns a conflicting MAC address.
- Include integration brand images in the release archive.

## [1.1.0] - 2026-10-28

### Added

- Link multiple device registry entries to one main device and shared MAC address.
- Edit the MAC address and linked device selection from the integration options.
- Unlink deselected devices while preserving MAC connections Device Linker did not add.
- Keep existing 1.0.0 entries editable through a backward-compatible settings reader.

## [1.0.0] - 2026-10-27

### Added

- Initial HACS release.
- Link two existing Home Assistant devices by adding their shared MAC connection.
- Select a target device, then choose a peer device or manual entry.
- Offer a selected peer’s registered MAC address for confirmation, with manual entry as a fallback.
- Restore the link when the owning integration refreshes the device registry entry.
- Preserve links during config-entry unloads and remove links when the Device Linker entry is deleted.

[1.1.1]: https://github.com/bisman-automations/ha-device-linker/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/bisman-automations/ha-device-linker/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/bisman-automations/ha-device-linker/releases/tag/v1.0.0
