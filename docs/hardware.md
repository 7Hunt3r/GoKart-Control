# Hardware inventory and evidence

| Item | Current information | Evidence status |
| --- | --- | --- |
| Kart count | Eight target; 1–3 development karts | Owner confirmed |
| Kart computer | Raspberry Pi 3 Model B V1.2 | Board silkscreen in photo 5; OS/runtime not inspected |
| Pit computer | Raspberry Pi 4 | Owner selected; not configured |
| VESC | Firmware 6.00, `75_100_V2`, `no_hw_limits`, controller ID 69 | USB read on one baseline kart |
| Commercial controller model | Unknown | Need physical label/product link; firmware ID is insufficient |
| VESC USB | Accessible, read successfully on Mac | Verified |
| VESC BLE | Preferred existing link; original code uses Nordic UART Service | Owner report + historical code; Heltec link/module identity unverified |
| Driver input | ADC and UART / Current Reverse Button | Configuration verified; physical wiring owner reported |
| Brakes | Mechanical pedal; motor braking limit 0 A | Owner report + runtime configuration |
| Motor temperature | No temperature sensors wired; sensing disabled | Owner confirmed + configuration |
| Motor sensing | FOC / Hall Sensors | Configuration; separate from temperature sensing |
| Motor model/rating | Unknown | Required for tuning |
| Battery | 72 V nominal / 84 V full; sampled 76.4 V | Nominal/full owner report; sample verified |
| Battery metadata | 20 cells, 20 Ah | Configured values, not battery/BMS rating verification |
| Battery/BMS rating | Unknown | Configured VESC BMS type is not proof of a connected BMS |
| Display | LESOWN L7 family, 7-inch IPS, 1024×600; HDMI video and USB-HID touch | Supplied guide Rev 2.1 + connector photos; exact suffix and live mode/touch still unverified |
| Heltec | HTIT-WB32LAF V4.3; packaging lists HF 863–928 | Photo-identified board; installed firmware and RF settings unverified |
| GPS | Quectel L76K with ceramic patch antenna, cabled to Heltec | Module marking and antenna visible; fix/serial operation untested |
| Base-station radio | LoRa attached to pit Pi 4 | Model/inventory pending |
| Pi/display/Heltec power | Regulated supply details unknown; Heltec must survive display power loss | Independent supply, USB backfeed and reset behavior need validation |
| Network | Pi 3 B has Wi-Fi/BLE and Ethernet; pit accessible locally | Board capability verified; site network not configured |
| Course imagery | Owner imagery or public CT ECO | No imagery/route selected or downloaded |

See the [photo evidence and connector guide](../hardware/photos/2026-11-05/README.md).
The supplied booklet says **L7 User Guide Rev 2.1**; the earlier LV marking was
a transcription, not an established PCB revision. Use **5V IN + TOUCH** for USB
touch data; the adjacent 5V IN connector is power-only.

Do not assign the captured controller to race kart 1 or assume controller ID 69
is a fleet-wide convention. Commission kart IDs and controller identities explicitly.
The controller's configured multi-ESC flag does not establish the physical CAN topology.

References: [Pi 3 B](https://www.raspberrypi.com/products/raspberry-pi-3-model-b/),
[Heltec V4](https://heltec.org/project/wifi-lora-32-v4/),
[CT ECO imagery](https://maps.cteco.uconn.edu/data/flight2023/).
