# Hardware photo evidence — November 5, 2026

Eight owner-supplied photos, copied unchanged. [Manifest](manifest.json) records
original filenames, sizes and SHA-256 hashes. These photos identify hardware;
they do not demonstrate powered operation, touch events, GPS fix or LoRa range.

| Photo | Evidence |
| --- | --- |
| [1: Heltec front / GNSS](01-heltec-front-l76k.jpg) | WiFi LoRa 32 V4 OLED board, USB-C, and a cabled module marked L76K. |
| [2: Heltec rear](02-heltec-rear-gps-antenna.jpg) | Silkscreen HTIT-WB32LAF V4.3, 8-wire GNSS connection, ceramic GPS patch antenna. |
| [3: packaging](03-heltec-packaging.jpg) | WiFi LoRa 32 V4; printed HF 863–928 range. This is packaging evidence, not a read of configured frequency. |
| [4: display front](04-display-front.jpg) | Landscape panel with corner mounts and attached cables. Screen not displaying a tested image. |
| [5: display rear / Pi](05-display-connectors-pi3.jpg) | 7-inch HDMI TFT board, speakers, HDMI IN, separate 5V IN and 5V IN + TOUCH USB ports; Pi silkscreen reads Raspberry Pi 3 Model B V1.2. |
| [6: guide cover](06-lesown-guide-cover.jpg) | LESOWN 7.0-inch HDMI IPS TFT Modules. |
| [7: guide pages 6–10](07-lesown-guide-pages-6-10.jpg) | L7 User Guide Rev 2.1; USB-HID touch guidance and requirement to connect the host to 5V IN + TOUCH. |
| [8: guide pages 2–5](08-lesown-guide-pages-2-5.jpg) | L7-family model table: 1024×600 IPS panels, touch/non-touch variants, power and interface descriptions. |

## Identification

- **Heltec:** HTIT-WB32LAF V4.3, with the pictured GPS daughterboard already attached.
- **GPS:** Quectel L76K module with ceramic patch antenna. No additional GPS module
  is required to implement the proposed design. GNSS serial operation is untested.
- **Display:** LESOWN L7 family, 7-inch IPS, 1024×600 per supplied manual; owner
  identifies this unit as a touchscreen. Capacitive USB-HID touch is documented
  for the touch variants. Exact product suffix is not readable/established here.
- **Revision correction:** 'L7 User Guide Rev 2.1' identifies the supplied manual.
  It is not evidence of a display-controller PCB revision called 'LV rev 2.1'.
- **Kart Pi:** Raspberry Pi 3 Model B V1.2 is visible on the actual board. The Pi 4
  box in the background does not change the identity of the pictured connected Pi.

## Connection plan supported by the evidence

1. Pi HDMI to display HDMI IN for video.
2. Pi USB data to the display port labeled **5V IN + TOUCH** for touch input.
   The neighboring **5V IN** port is power-only and cannot carry touch events.
3. Pi USB to Heltec USB-C for the proposed serial bridge. Firmware still needs
   identification/implementation; the photos do not establish a working bridge.
4. Keep L76K on the Heltec GNSS connection. Verify exact V4.3 pin assignment,
   power/reset control and serial settings against the schematic before firmware.
5. VESC link remains BLE, independent of those USB connections.

The supplied manual describes USB-HID touch without a vendor driver. Actual
Linux device enumeration, EDID/display mode, touch mapping and orientation still
need a bench check. Its old Raspberry Pi config.txt examples are historical
installation guidance, not configuration changes to apply blindly to a new OS.

The display and Pi require a regulated accessory supply; the photos do not
establish the kart's supply current capacity or full power budget. Do not infer
that every device can be powered reliably through one Pi USB port.

## Still unverified

- Actual display EDID/native mode, touch operation and chosen mounted orientation.
- Exact display product suffix; 1024×600 is manual-derived pending live confirmation.
- Heltec installed firmware, GPS output/fix/baud, and serial bridge behavior.
- Actual RF frequency/power settings and antenna specification. Packaging supports
  the high-band identification; no radio channel has been configured by this project.
- Whether all development units match the pictured hardware.

## Manufacturer references

- [Heltec WiFi LoRa 32 V4](https://heltec.org/project/wifi-lora-32-v4/): ESP32-S3/SX1262 family and GNSS interface.
- [Exact V4.3 schematic](https://resource.heltec.cn/download/WiFi_LoRa_32_V4/Schematic/HTIT-WB32LAF_V4.3.pdf).
- [Heltec expansion reference](https://heltec.org/project/wifi-lora-32-v4-expansion-housing/): Quectel L76K option; not a claim that the pictured separate module is the complete expansion housing kit.
- LESOWN primary evidence is the supplied photographed manufacturer guide above;
  no matching exact-suffix online datasheet was established.
