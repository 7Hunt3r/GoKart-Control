// config.h — LoRa repeater build configuration (same board as the base) (LilyGo T-Beam S3 Core / "Supreme").
// Pins from the Meshtastic tbeam-s3-core variant (runs on this exact board) and the
// LilyGo LoRa-Series board support. The AXP2101 power manager must enable the LoRa
// and GNSS rails before either chip answers; the rail map is from Meshtastic Power.cpp.
#pragma once

#define FW_VERSION "repeater-0.1.0"

// LoRa (SX1262 module on the Supreme; LR1121 variants exist and are NOT handled yet)
#define PIN_LORA_CS    10
#define PIN_LORA_SCK   12
#define PIN_LORA_MOSI  11
#define PIN_LORA_MISO  13
#define PIN_LORA_RST   5
#define PIN_LORA_DIO1  1
#define PIN_LORA_BUSY  4
#define LORA_TCXO_V    1.8
#define LORA_TX_POWER_DBM 20   // SX1262 direct to antenna, no external amplifier

// GNSS (u-blox M10 on the Supreme)
#define PIN_GNSS_RX    9       // ESP32 receives here
#define PIN_GNSS_TX    8
#define PIN_GNSS_PPS   6
#define PIN_GNSS_WAKE  7

// Power manager (AXP2101) and RTC share Wire1
#define PIN_PMU_SDA    42
#define PIN_PMU_SCL    41

// OLED (SH1106 1.3", optional) and sensors on Wire
#define PIN_OLED_SDA   17
#define PIN_OLED_SCL   18

#define PIN_BUTTON     0

#define OLED_REFRESH_MS   500
#define STAT_PERIOD_MS    1000
#define KART_ONLINE_MS    5000
#define GPS_DETECT_STEP_MS 4000
#define WDT_TIMEOUT_S     20
#define RELAY_QUEUE_LEN   8
#define RELAY_SEEN_LEN    32
#define RELAY_SEEN_MS     5000
