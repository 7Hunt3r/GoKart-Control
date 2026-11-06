/*
 * GoKart Control — plug-anywhere LoRa repeater for LilyGo T-Beam S3 Core.
 *
 * Listens on the fleet channel and re-sends every packet it hears exactly once:
 *   - kart telemetry: GK_RELAY_DELAY_MS after reception, which lands inside the same
 *     kart's 50 ms slot (22 ms packet + 25 ms + 22 ms copy), so no other kart is disturbed;
 *   - pit beacon and commands: same delay; karts dedupe commands by seq, and compensate
 *     the beacon's extra hop for slot timing.
 * Copies carry GK_HDR_RELAY_BIT, so a repeater never relays a relayed packet (one hop) and
 * the pit knows a packet came via the repeater. No configuration: power it and leave it.
 * One repeater per area; two in range of each other would relay the same packet at once.
 *
 * OLED: heard/relayed counters, pit beacon RSSI, battery. USB: JSON stat lines once a second.
 * Status: compiles; first unit flashed 2026-11-06.
 */
#include <Arduino.h>
#include <SPI.h>
#include <Wire.h>
#include <RadioLib.h>
#include <U8g2lib.h>
#include <XPowersLib.h>
#include <esp_task_wdt.h>
#include <GoKartProtocol.h>
#include "config.h"

XPowersLibInterface* pmu = nullptr; bool pmuOk = false;
SX1262 radio = new Module(PIN_LORA_CS, PIN_LORA_DIO1, PIN_LORA_RST, PIN_LORA_BUSY);
volatile bool loraIrq = false; volatile uint32_t loraIrqMicros = 0;
void IRAM_ATTR onLoraIrq() { loraIrq = true; loraIrqMicros = micros(); }
bool radioOk = false; int radioStatus = -999;
uint32_t heard = 0, relayed = 0, rxBad = 0, dropped = 0, dupes = 0;
uint32_t heardKart[GK_MAX_KARTS + 1] = {0}, relayedKart[GK_MAX_KARTS + 1] = {0};
uint32_t kartMs[GK_MAX_KARTS + 1] = {0};
float    kartRssi[GK_MAX_KARTS + 1] = {0};
uint32_t baseHeardMs = 0; float baseRssi = 0;

struct Pending { uint8_t buf[64]; uint8_t len; uint32_t dueMicros; bool used; };
Pending queue[RELAY_QUEUE_LEN]; int qHead = 0, qTail = 0;
struct Seen { uint8_t src, seq, type; uint32_t ms; };
Seen seen[RELAY_SEEN_LEN]; int seenIdx = 0;

U8G2_SH1106_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE, PIN_OLED_SCL, PIN_OLED_SDA);
bool oledOk = false; uint32_t oledMs = 0, statMs = 0;

static void logf(const char* fmt, ...) {
  char msg[160]; va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
  for (char* p = msg; *p; p++) if (*p == '"' || *p == '\\') *p = '\'';
  Serial.printf("{\"t\":\"log\",\"msg\":\"%s\"}\n", msg);
}

static void pmuInit() {
  Wire1.begin(PIN_PMU_SDA, PIN_PMU_SCL);
  pmu = new XPowersAXP2101(Wire1, PIN_PMU_SDA, PIN_PMU_SCL);
  pmuOk = pmu->init();
  if (!pmuOk) { delete pmu; pmu = nullptr; return; }
  pmu->setPowerChannelVoltage(XPOWERS_ALDO3, 3300); pmu->enablePowerOutput(XPOWERS_ALDO3);   // LoRa
  pmu->setPowerChannelVoltage(XPOWERS_ALDO1, 3300); pmu->enablePowerOutput(XPOWERS_ALDO1);   // OLED/sensors
  pmu->setPowerChannelVoltage(XPOWERS_ALDO2, 3300); pmu->enablePowerOutput(XPOWERS_ALDO2);
  pmu->setPowerChannelVoltage(XPOWERS_DCDC3, 3300); pmu->enablePowerOutput(XPOWERS_DCDC3);
  pmu->disablePowerOutput(XPOWERS_ALDO4);           // GNSS not needed on a repeater: save battery
  pmu->disablePowerOutput(XPOWERS_BLDO1); pmu->disablePowerOutput(XPOWERS_BLDO2);
  pmu->disablePowerOutput(XPOWERS_DCDC2); pmu->disablePowerOutput(XPOWERS_DCDC5);
  pmu->disableTSPinMeasure();
  pmu->setChargerConstantCurr(XPOWERS_AXP2101_CHG_CUR_500MA);
  pmu->setChargeTargetVoltage(XPOWERS_AXP2101_CHG_VOL_4V2);
  pmu->enableBattVoltageMeasure(); pmu->disableIRQ(XPOWERS_AXP2101_ALL_IRQ);
  delay(100);
}

static bool seenBefore(uint8_t src, uint8_t seq, uint8_t type) {
  uint32_t now = millis();
  for (int i = 0; i < RELAY_SEEN_LEN; i++)
    if (seen[i].ms && now - seen[i].ms < RELAY_SEEN_MS && seen[i].src == src && seen[i].seq == seq && seen[i].type == type) return true;
  seen[seenIdx] = { src, seq, type, now }; seenIdx = (seenIdx + 1) % RELAY_SEEN_LEN;
  return false;
}

static void serviceRx() {
  if (!radioOk || !loraIrq) return;
  loraIrq = false;
  uint32_t rxMicros = loraIrqMicros;
  uint8_t buf[64]; size_t len = radio.getPacketLength();
  if (len == 0 || len > sizeof buf) { radio.startReceive(); return; }
  int st = radio.readData(buf, len); float rssi = radio.getRSSI();
  radio.startReceive();
  if (st != RADIOLIB_ERR_NONE) { rxBad++; return; }
  uint8_t type = gkValidate(buf, len);
  if (!type) { rxBad++; return; }
  GkHeader* h = (GkHeader*)buf;
  heard++;
  if (gkIsRelayed(*h)) return;                               // one hop only
  if (h->src == GK_ADDR_BASE) { baseHeardMs = millis(); baseRssi = rssi; }
  else if (h->src <= GK_MAX_KARTS) { heardKart[h->src]++; kartMs[h->src] = millis(); kartRssi[h->src] = rssi; }
  if (seenBefore(h->src, h->seq, type)) { dupes++; return; }
  int next = (qTail + 1) % RELAY_QUEUE_LEN;
  if (next == qHead) { dropped++; return; }
  Pending& p = queue[qTail];
  memcpy(p.buf, buf, len); p.len = (uint8_t)len; p.used = true;
  ((GkHeader*)p.buf)->version |= GK_HDR_RELAY_BIT;
  p.dueMicros = rxMicros + (uint32_t)GK_RELAY_DELAY_MS * 1000UL;
  qTail = next;
}

static void serviceTx() {
  if (qHead == qTail || !radioOk) return;
  Pending& p = queue[qHead];
  if ((int32_t)(micros() - p.dueMicros) < 0) return;
  int st = radio.transmit(p.buf, p.len);
  loraIrq = false; radio.startReceive();
  GkHeader* h = (GkHeader*)p.buf;
  if (st == RADIOLIB_ERR_NONE) { relayed++; if (h->src >= 1 && h->src <= GK_MAX_KARTS) relayedKart[h->src]++; }
  p.used = false; qHead = (qHead + 1) % RELAY_QUEUE_LEN;
}

static void serviceStat() {
  if (millis() - statMs < 1000) return; statMs = millis();
  char karts[160] = ""; size_t n = 0;
  for (int k = 1; k <= GK_MAX_KARTS; k++) if (kartMs[k] && millis() - kartMs[k] < 10000) n += snprintf(karts + n, sizeof karts - n, "%s{\"kart\":%d,\"rssi\":%.0f,\"heard\":%lu,\"relayed\":%lu}", n ? "," : "", k, kartRssi[k], (unsigned long)heardKart[k], (unsigned long)relayedKart[k]);
  Serial.printf("{\"t\":\"stat\",\"fw\":\"%s\",\"radio\":%s,\"heard\":%lu,\"relayed\":%lu,\"dupes\":%lu,\"dropped\":%lu,\"rx_bad\":%lu,\"pit_rssi\":%s,\"pit_age_s\":%s,\"batt_v\":%.2f,\"uptime_s\":%lu,\"karts\":[%s]}\n",
                FW_VERSION, gkBool(radioOk), (unsigned long)heard, (unsigned long)relayed, (unsigned long)dupes, (unsigned long)dropped, (unsigned long)rxBad,
                baseHeardMs ? String((int)baseRssi).c_str() : "null", baseHeardMs ? String((unsigned long)((millis() - baseHeardMs) / 1000)).c_str() : "null",
                (pmuOk && pmu) ? pmu->getBattVoltage() / 1000.0 : 0.0, (unsigned long)(millis() / 1000), karts);
}

static void serviceOled() {
  if (!oledOk || millis() - oledMs < 500) return; oledMs = millis();
  char l[32]; uint32_t now = millis();
  oled.clearBuffer(); oled.setFont(u8g2_font_7x14B_tf); oled.drawStr(0, 13, "GOKART REPEATER");
  oled.setFont(u8g2_font_6x10_tf);
  snprintf(l, sizeof l, "heard %lu relayed %lu", (unsigned long)heard, (unsigned long)relayed); oled.drawStr(0, 26, l);
  if (baseHeardMs) snprintf(l, sizeof l, "pit %.0fdBm %lus ago", baseRssi, (unsigned long)((now - baseHeardMs) / 1000)); else snprintf(l, sizeof l, "pit -- not heard");
  oled.drawStr(0, 37, l);
  int y = 48;
  for (int k = 1; k <= GK_MAX_KARTS && y <= 64; k++) if (kartMs[k] && now - kartMs[k] < 10000) { snprintf(l, sizeof l, "K%d %.0fdBm", k, kartRssi[k]); oled.drawStr(((k - 1) % 2) * 64, y, l); if ((k - 1) % 2) y += 11; }
  if (!radioOk) oled.drawStr(0, 64, "RADIO FAILED");
  oled.sendBuffer();
}

void setup() {
  Serial.begin(115200);
  pmuInit();
  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL); oledOk = oled.begin();
  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_CS);
  radioStatus = radio.begin(GK_LORA_FREQ_MHZ, GK_LORA_BW_KHZ, GK_LORA_SF, GK_LORA_CR, GK_LORA_SYNC_WORD, LORA_TX_POWER_DBM, GK_LORA_PREAMBLE, LORA_TCXO_V, false);
  if (radioStatus == RADIOLIB_ERR_NONE) { radio.setDio2AsRfSwitch(true); radio.setCurrentLimit(140); radio.setPacketReceivedAction(onLoraIrq); radio.startReceive(); radioOk = true; }
  esp_task_wdt_config_t wdt = { .timeout_ms = WDT_TIMEOUT_S * 1000, .idle_core_mask = 0, .trigger_panic = true };
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
  esp_task_wdt_add(NULL);
  logf("boot %s (built " __DATE__ " " __TIME__ ") pmu %s radio %s (%d)", FW_VERSION, pmuOk ? "ok" : "MISSING", radioOk ? "ok" : "FAILED", radioStatus);
}

void loop() {
  esp_task_wdt_reset();
  serviceRx();
  serviceTx();
  serviceRx();
  serviceStat();
  serviceOled();
  delay(1);
}
