// config.h — kart node build configuration (Heltec WiFi LoRa 32 V4.3 + Quectel L76K).
// Pins come from the HTIT-WB32LAF V4.3 schematic and the Meshtastic heltec_v4
// variant (which runs on this exact board). Everything else is this project's choice.
#pragma once

#define FW_VERSION      "kart-0.1.2-config-guard-2hz"
#define DEFAULT_KART_ID 1            // runtime override: USB command "id N" (stored in flash)

// ---- VESC writes -----------------------------------------------------------
// 1 = this firmware may send COMM_SET_MCCONF_TEMP (mode / stop / start) when a
//     command arrives from the pit or over USB. It NEVER writes on its own, never
//     on boot, never on reconnect (owner reliability rule).
// 0 = telemetry-only build; every write command is answered REJECTED.
#ifndef ENABLE_VESC_WRITES
#define ENABLE_VESC_WRITES 1
#endif

// ---- VESC transport --------------------------------------------------------
// 0 = BLE client to the VESC's Nordic-UART module (fleet default, owner decision 2026-11-05).
//     The kart connects ONLY to the VESC address pinned with the USB command
//     "vescmac AA:BB:CC:DD:EE:FF". Unpinned, it scans, reports every "VESC BLE UART" it
//     sees (address + RSSI) over USB and on the OLED, and does not connect. Eight karts
//     in one paddock make name-based selection unsafe (docs/architecture.md).
// 1 = wired UART: VESC COMM port TX -> VESC_UART_RX_PIN, RX <- VESC_UART_TX_PIN, GND-GND.
#ifndef VESC_TRANSPORT_UART
#define VESC_TRANSPORT_UART 0
#endif
#define BLE_SCAN_MS        2500   // scan window while disconnected
#define BLE_SCAN_EVERY_MS  6000
#define VESC_UART_RX_PIN   48        // free GPIO on header J2 pin 14
#define VESC_UART_TX_PIN   47        // free GPIO on header J2 pin 13
#define VESC_UART_BAUD     115200    // matches app_uart_baudrate in the baseline capture
#define VESC_BLE_NAME      "VESC BLE UART"
#define VESC_POLL_MS       200
#define VESC_LINK_TIMEOUT_MS   1500
#define VESC_MCCONF_REFRESH_MS 10000 // periodic read-only re-read of live limits
#define VESC_ACK_TIMEOUT_MS    700

// ---- Heltec V4.3 pins --------------------------------------------------------
#define PIN_LORA_NSS   8
#define PIN_LORA_SCK   9
#define PIN_LORA_MOSI  10
#define PIN_LORA_MISO  11
#define PIN_LORA_RST   12
#define PIN_LORA_BUSY  13
#define PIN_LORA_DIO1  14
#define PIN_FEM_POWER  7    // VFEM_Ctrl: HIGH enables the TLV75733 LDO feeding the KCT8103L front end
#define PIN_FEM_CSD    2    // KCT8103L CSD: HIGH = chip enabled
#define PIN_FEM_CTX    5    // KCT8103L CTX: HIGH during TX, LOW = RX LNA. SX1262 DIO2 drives CPS.
#define LORA_TCXO_V    1.8
#define LORA_TX_POWER_DBM 10 // SX1262 output; the V4.3 front end adds roughly +7..+11 dB on top

#define PIN_OLED_SDA   17
#define PIN_OLED_SCL   18
#define PIN_OLED_RST   21
#define PIN_VEXT       36   // active LOW: powers the OLED
#define PIN_LED        35
#define PIN_BUTTON     0

#define PIN_GNSS_POWER 34   // VGNSS_Ctrl, active LOW (P-FET)
#define PIN_GNSS_RST   42   // L76K reset, LOW > 100 ms
#define PIN_GNSS_WAKE  40   // HIGH = force wake
#define PIN_GNSS_PPS   41
#define PIN_GNSS_RX    39   // ESP32 receives here (labelled GNSS_TX on the pin map)
#define PIN_GNSS_TX    38   // ESP32 transmits here (labelled GNSS_RX)
// If the L76K wiring turns out swapped, the firmware detects it automatically.

// Temporary writes preserve limits from a fresh GET_MCCONF reply. No BASE_*
// constants are sent back to the controller. A pinned VESC UUID/6.00 is required.

// ---- Modes ----------------------------------------------------------------
// Ported from the Core2 dashboard: motor-current scale relative to the 140 A
// configured maximum. Owner's eventual intent is top-speed caps instead; that
// is a separate change once the drivetrain numbers are verified.
struct ModeDef { const char* name; float currentMaxScale; };
static const ModeDef MODES[3] = {
  { "ECO",    80.0f  / 140.0f },
  { "NORMAL", 100.0f / 140.0f },
  { "SPORT",  1.0f },
};
#define MODE_MATCH_TOL 0.015f

// Stop steps are upper bounds, clamped to each fresh live scale so they never
// increase the allowance. Mechanical brakes are untouched.
static const float STOP_RAMP[] = { 0.5f, 0.2f, 0.0f };
#define STOP_RAMP_STEP_MS 150

// ---- Timing ----------------------------------------------------------------
#define OLED_REFRESH_MS    500
#define USB_SELF_REPORT_MS 200 // 5 Hz local Pi display feed
#define GPS_DETECT_STEP_MS 4000
#define FLEET_STALE_MS     5000
#define WDT_TIMEOUT_S      20
