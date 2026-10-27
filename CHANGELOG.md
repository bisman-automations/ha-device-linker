# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-10-27

### Added

- Initial HACS release.
- Link two existing Home Assistant devices by adding their shared MAC connection.
- Offer a peer device’s registered MAC address automatically, with manual entry when none is available.
- Restore the link when the owning integration refreshes the device registry entry.
- Preserve links during config-entry unloads and remove links when the Device Linker entry is deleted.

[1.0.0]: https://github.com/bisman-automations/ha-device-linker/releases/tag/v1.0.0
