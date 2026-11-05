# Historical reference: previous iteration

The owner explicitly prioritizes the connected kart over this repository. See
[the actual baseline](../hardware/vesc/baseline-kart/2026-11-05/README.md) before
using any values or assumptions from this historical source.

Reviewed source commit: 4b11c94b83a34e8cb35b999fda783a9aa3b50686.
Repository: https://github.com/eamondoherty618-prog/vesc-ble-dashboard
Files reviewed: README.md, VescDashboard/VescDashboard.ino, VescScan/VescScan.ino.

## What exists

Arduino C++ for an M5Stack Core2 with a 320x240 touchscreen. It scans for the BLE
name VESC BLE UART, uses Nordic UART Service characteristics, and polls
COMM_GET_VALUES every 600 ms. It displays absolute RPM from the VESC library,
MOSFET temperature, pack voltage, and voltage times average input current.

Three touch buttons request ECO, NORMAL, and SPORT via COMM_SET_MCCONF_TEMP.
There is a standalone BLE scan utility. No LoRa, GPS, map, fleet management, or
session logging is implemented in that repository.

## Carry forward

- VESC telemetry field selection and Nordic UART UUIDs.
- The three profile names and their user-facing purpose.
- Automatic connection recovery as a requirement, with better state handling.

M5Unified drawing and NimBLE-Arduino cannot be used unchanged as a Linux Pi app.
Port the protocol behavior and product features behind platform-specific adapters.

## Findings to resolve during the port

1. inCurrentMax and SPORT_INPUT_A are declared but unused when encoding a map.
   The README's battery-current limits therefore are not transmitted.
2. The described field-weakening changes are absent. All profiles send the same
   very broad watt bounds; the NORMAL watt cap described in comments is absent.
3. SPORT sends -1 for the minimum-current scale and broad limits, describing
   these as restore sentinels. Current upstream VESC commands.c clamps current
   scales to 0..1 rather than treating -1 as a restore instruction. Do not
   copy this behavior without checking the actual installed firmware.
4. UI activeMap changes immediately and does not process a map acknowledgement
   or verify the resulting configuration. SPORT is initially shown without an
   initial application or readback. Reconnect reapplies the selected map.
5. Telemetry failures render zero readings. The port needs explicit invalid and
   stale states, especially for speed and vehicle status.
6. BLE selection by a common advertised name is unsuitable for eight nearby
   karts. Commission and persist controller identities.
7. BLE writes do not handle negotiated chunk sizes and the receive ring buffer
   has no explicit overflow handling. Design and test bounded streaming parsers.

Current upstream reference (not proof of the installed controller's behavior):
https://github.com/vedderb/bldc/blob/master/comm/commands.c

The old current values are historical examples, not approved limits for the
new system. Retrieve and verify the kart's actual configuration before enabling
profile writes. Preserve the requested/applied distinction in the interface.
