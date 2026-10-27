# Device Linker

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8.0%20%2B-blue.svg)](https://www.home-assistant.io/)
[![GitHub Release](https://img.shields.io/github/v/release/bisman-automations/ha-device-linker)](https://github.com/bisman-automations/ha-device-linker/releases)
[![Test](https://github.com/bisman-automations/ha-device-linker/actions/workflows/test.yml/badge.svg)](https://github.com/bisman-automations/ha-device-linker/actions/workflows/test.yml)
[![Validate](https://github.com/bisman-automations/ha-device-linker/actions/workflows/validate.yml/badge.svg)](https://github.com/bisman-automations/ha-device-linker/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/bisman-automations/ha-device-linker)](LICENSE)

Device Linker adds a shared MAC address connection to an existing Home Assistant device so Home Assistant can show a corresponding device from another integration under **Linked devices**. It links registry entries; it does not merge devices or synchronize their entities.

## Requirements

- Home Assistant 2026.8.0 or newer.
- The two devices must be registered by different config entries and share a MAC address.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and add this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=bisman-automations&repository=ha-device-linker&category=integration)

1. Select the button above, or in HACS open the menu, choose **Custom repositories**, and add `https://github.com/bisman-automations/ha-device-linker` as an **Integration**.
2. Download **Device Linker** and restart Home Assistant.

### Manual

1. Download `device_linker.zip` from the [latest release](https://github.com/bisman-automations/ha-device-linker/releases/latest).
2. Unzip its `custom_components/device_linker` folder into `config/custom_components/` and restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up Device Linker.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=device_linker)

Select the button above, or go to **Settings > Devices & services > Add integration** and search for **Device Linker**. Choose the device you want to link and the corresponding peer device. If the peer integration has registered a MAC address in Home Assistant’s Device Registry, Device Linker offers it for confirmation. If the peer has multiple registered MAC addresses, choose the correct one. If no MAC address is registered, enter it manually. Colons and hyphens are accepted as separators.

Home Assistant requires a connection to be unique within a config entry, so the two devices must belong to different config entries. If another device in the target device’s config entry already uses the MAC address, choose a different MAC or target device. If both devices already share a registry identifier or connection, Home Assistant already considers them linked.

## Troubleshooting

- **The other device is not listed:** Check that both integrations have registered their devices and that they use the same MAC address.
- **The devices are still separate:** This integration only adds a registry connection. Home Assistant displays links between eligible devices; it does not combine their entities, states, or controls.
- **Diagnostics:** On the integration's page, open the menu and choose **Download diagnostics**. Review the file for anything you do not want to share before attaching it to an issue.
- **Debug logs:** On the integration's page choose **Enable debug logging**, reproduce the problem, then **Disable debug logging** to download the log.
- Report problems at [GitHub Issues](https://github.com/bisman-automations/ha-device-linker/issues).

## Development

```sh
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest
```

CI tests the oldest supported Home Assistant version and runs hassfest and HACS validation. Releases are drafted from `CHANGELOG.md` on pushes to `main`; a maintainer publishes them.

## License

[MIT](LICENSE)
