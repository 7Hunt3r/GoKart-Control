// config.h — kart node build configuration (Heltec WiFi LoRa 32 V4.3 + Quectel L76K).
// Pins come from the HTIT-WB32LAF V4.3 schematic and the Meshtastic heltec_v4
// variant (which runs on this exact board). Everything else is this project's choice.
#pragma once

#define FW_VERSION      "kart-0.2.6"
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
// SX1262 drive into the KCT8103L front end. Radiated = drive + front-end gain, Heltec's table as
// carried in Meshtastic's LoRaFEMInterface.cpp: drive 0-13 dBm -> +13 dB, 14-15 -> +12, 16-17 -> +11,
// 18 -> +10, 19 -> +9, 20 -> +8, 21 -> +7. So 10 -> 23 dBm, 14 -> 26, 16 -> 27, 18 and up -> 28
// (the board's rating, 28 +/- 1 dBm; Heltec's own field test ran the same part at 11 -> ~22).
// The datasheet's supply current is ~400 mA at 22 dBm and ~750 mA at 27 dBm for the 23 ms packet.
// Default 16 = ~27 dBm. The console's `txpower N` (stored) overrides it; go back to 10-14 if the
// kart Pi's 5 V sags (USB resets in the Pi's dmesg) until the Heltec has its own 5 V supply.
#define LORA_TX_POWER_DBM 16

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

// ---- Baseline limits (hardware/vesc/baseline-kart/2026-11-05) ---------------
// Sent back unchanged in every temporary-limit write so only the current scale
// moves. Battery current limits are never sent (the VESC keeps its own).
#define BASE_CURRENT_MIN_SCALE  1.0f
#define BASE_MIN_ERPM     -150000.0f
#define BASE_MAX_ERPM      150000.0f
#define BASE_MIN_DUTY      0.005f
#define BASE_MAX_DUTY      0.95f
#define BASE_WATT_MIN   -1500000.0f
#define BASE_WATT_MAX    1500000.0f

// ---- Modes ----------------------------------------------------------------
// A mode is a TOP-SPEED CAP plus a TORQUE (motor-current) SCALE, both written as temporary
// limits (store=0) on top of the controller's own configuration:
//   * topMph          -> l_max_erpm (and the reverse bound), computed the way SLOW does it from
//                        THIS controller's own si_motor_poles / si_gear_ratio / si_wheel_diameter
//                        (baseline kart: 14 poles, 2.769, 0.406 m -> 15 mph = 6115 ERPM). 0 = no cap,
//                        the controller's own ERPM bound stays. The VESC tapers the current over the
//                        last 20 % below the bound (l_erpm_start 0.8), so the cap is a soft ceiling.
//   * currentMaxScale -> l_current_max_scale, relative to l_current_max (140 A in the baseline).
// Why both: the Core2 dashboard's modes only moved the current scale (80 / 100 / 140 A) and the
// three felt the same on the kart (Eamon, kart 2, 2026-11-07): a current cap only shapes the
// first second of a launch; after that the motor is out of voltage, not current, and the top
// speed is identical. The owner's stated intent (docs/requirements.md) is top-speed caps with
// strong acceleration. First cut, for the owner to confirm after driving; edit here only (the
// pit, kart Pi and simulators carry copies that firmware/tests/test_protocol.py checks against this):
struct ModeDef { const char* name; float currentMaxScale; uint8_t topMph; };
static const ModeDef MODES[3] = {
  { "ECO",    0.50f, 15 },   //  70 A of 140, 15 mph
  { "NORMAL", 0.75f, 25 },   // 105 A of 140, 25 mph
  { "SPORT",  1.00f,  0 },   // 140 A, the controller's own ERPM bound (no cap)
};
#define MODE_MATCH_TOL  0.015f   // a live scale this close to a mode's reads as that mode ...
#define MODE_ERPM_TOL   50.0f    // ... when the live ERPM bound is also this close to the mode's cap

// ---- speed calibration -------------------------------------------------------
// The caps are turned into ERPM with the controller's own si_* numbers (poles / gear / wheel). On the
// MotoTec karts those numbers are off by about 2.5x: on 2026-11-09 kart 2's GPS read 0.00159 km/h per
// ERPM where the configured 14 poles / 2.769 / 0.406 m predict 0.00395, so the 15 and 25 mph caps were
// really 6 and 10 mph on the road. SPEED_CAL multiplies the ERPM the drivetrain math gives for a speed
// (real ERPM = configured-math ERPM x SPEED_CAL). Default = that measurement; the console's
// `speedcal X` (0.3..5, stored per kart) overrides it, `speedcal clear` returns to the default; the
// status line reports speed_cal. The proper fix is the si_* values in VESC Tool; then set speedcal 1.0.
#define SPEED_CAL_DEFAULT 2.48f
// A BOOST (timed scale ABOVE the mode's) also lifts the speed cap for its duration, to the highest mode
// whose scale the boost reaches (owner 2026-11-09: in ECO a 0.75 boost = NORMAL's 25 mph, a 1.0 ROCKET =
// SPORT, uncapped; an in-between scale lifts one mode). The game only ever sends boost >= the mode's scale.
#define BOOST_LIFTS_CAP 1

// Stop = ramp the current scale down, then hold at 0. Mechanical brakes are untouched.
// At the same time the kart asks the VESC's brake script (firmware/vesc-lisp/gokart_brake.lisp)
// for regen braking; without that script loaded, or with Motor Current Max Brake = 0, the
// request is a no-op and the kart coasts.
#define STOP_BRAKE_AMPS   40     // requested brake current, A (the VESC clamps to its own brake limit)
#define STOP_BRAKE_SECS   2.5f   // how long the script brakes before releasing to the pedal app
// Slow = cap the ERPM limit to a speed (mph) using the controller's own poles/gear/wheel values.
#define SLOW_DEFAULT_MPH  5
static const float STOP_RAMP[] = { 0.5f, 0.2f, 0.0f };
#define STOP_RAMP_STEP_MS 150   // ~0.5 s to zero torque plus ack/verify

// ---- Timing ----------------------------------------------------------------
#define OLED_REFRESH_MS    500
#define USB_SELF_REPORT_MS 500
#define GPS_DETECT_STEP_MS 4000
#define FLEET_STALE_MS     5000
#define WDT_TIMEOUT_S      20
