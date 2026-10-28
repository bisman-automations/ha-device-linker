# Device Linker

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8.0%20%2B-blue.svg)](https://www.home-assistant.io/)
[![GitHub Release](https://img.shields.io/github/v/release/bisman-automations/ha-device-linker)](https://github.com/bisman-automations/ha-device-linker/releases)
[![Test](https://github.com/bisman-automations/ha-device-linker/actions/workflows/test.yml/badge.svg)](https://github.com/bisman-automations/ha-device-linker/actions/workflows/test.yml)
[![Validate](https://github.com/bisman-automations/ha-device-linker/actions/workflows/validate.yml/badge.svg)](https://github.com/bisman-automations/ha-device-linker/actions/workflows/validate.yml)
[![License](https://img.shields.io/github/license/bisman-automations/ha-device-linker)](LICENSE)

Device Linker adds a shared MAC address connection to multiple selected Home Assistant devices so Home Assistant can show them together under **Linked devices**. It links registry entries; it does not merge devices or synchronize their entities.

## Requirements

- Home Assistant 2026.8.0 or newer.
- The main device and each linked device must be registered by different config entries and share a MAC address.

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

Select the button above, or go to **Settings > Devices & services > Add integration** and search for **Device Linker**. Choose the main device that already has the MAC address; registered MAC addresses are shown next to device names. Or select **Manual MAC entry** if the address is not registered. Next, select one or more devices to link, then confirm the MAC address. You can enter a MAC manually at confirmation. Colons and hyphens are accepted as separators.

Home Assistant requires a connection to be unique within a config entry, so the main device and each linked device must belong to different config entries. Device Linker also requires selected linked devices to come from separate config entries. If a device from one of the selected integrations already uses the MAC address, choose a different MAC or device.

## Editing and unlinking

Open a Device Linker entry under **Settings > Devices & services > Integrations**, then choose **Configure**. The main device stays fixed; change the MAC address or linked-device selection. Remove a device from the selection to unlink it; clearing the selection unlinks all devices. Device Linker removes only MAC connections it added itself.

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
