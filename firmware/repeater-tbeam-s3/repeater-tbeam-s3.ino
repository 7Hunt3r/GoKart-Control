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
#include <Preferences.h>
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
uint32_t hbMs = 0, hbTx = 0; uint8_t hbSeq = 0;

struct Pending { uint8_t buf[64]; uint8_t len; uint32_t dueMicros; bool used; };
Pending queue[RELAY_QUEUE_LEN]; int qHead = 0, qTail = 0;
struct Seen { uint8_t src, seq, type; uint32_t ms; };
Seen seen[RELAY_SEEN_LEN]; int seenIdx = 0;

// The 1.3" module on the first T-Beam is an SH1106; the 0.96" module on the second is an SSD1306. An SSD1306
// given SH1106 init stays dark (its charge pump is never enabled) while still answering on I2C, so the
// driver is selectable and saved: USB command `oled ssd1306` or `oled sh1106` (reboots).
Preferences prefs; String oledDrv; uint8_t oledAddr = 0x3C; uint8_t id3c = 0xFF;
U8G2* oled = nullptr;

static uint8_t i2cReadReg(uint8_t addr, uint8_t reg) {      // one register read; 0xFF when nobody answers
  Wire.beginTransmission(addr); Wire.write(reg); if (Wire.endTransmission(false) != 0) return 0xFF;
  if (Wire.requestFrom((int)addr, 1) != 1) return 0xFF; return Wire.read();
}
bool oledOk = false; uint32_t oledMs = 0, statMs = 0; char i2cFound[64] = "";

static void i2cScan() {                      // what answers on the OLED/sensor bus (SH1106 is 0x3c or 0x3d)
  size_t n = 0; i2cFound[0] = 0;
  for (uint8_t a = 0x08; a < 0x78 && n < sizeof i2cFound - 6; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) n += snprintf(i2cFound + n, sizeof i2cFound - n, "%s0x%02x", n ? " " : "", a);
  }
  if (!n) strcpy(i2cFound, "none");
}

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
  // Same rails as the working base sketch otherwise (BLDO1 = SD card, BLDO2 unknown load on this board).
  pmu->setPowerChannelVoltage(XPOWERS_BLDO1, 3300); pmu->enablePowerOutput(XPOWERS_BLDO1);
  pmu->setPowerChannelVoltage(XPOWERS_BLDO2, 3300); pmu->enablePowerOutput(XPOWERS_BLDO2);
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
  bool wasBeacon = (h->src == GK_ADDR_BASE && h->type == GK_MSG_BASE_INFO);
  p.used = false; qHead = (qHead + 1) % RELAY_QUEUE_LEN;
  // Heartbeat to the pit every 5 s, right behind the beacon copy: still inside the pit's own window
  // (beacon 905 + 12 ms, copy at ~945-957, heartbeat ~960-970 ms, karts start at 1000).
  if (wasBeacon && st == RADIOLIB_ERR_NONE && millis() - hbMs >= 5000) {
    hbMs = millis();
    GkRepeaterInfo r; memset(&r, 0, sizeof r);
    gkFillHeader(r.h, GK_MSG_REPEATER_INFO, GK_ADDR_REPEATER, GK_ADDR_BASE, hbSeq++);
    r.h.version |= GK_HDR_RELAY_BIT;                      // never relayed by another repeater
    r.heard = (uint16_t)heard; r.relayed = (uint16_t)relayed;
    r.batt_mv = (pmuOk && pmu) ? (uint16_t)pmu->getBattVoltage() : 0;
    r.pit_rssi = (int8_t)baseRssi;
    for (int k = 1; k <= GK_MAX_KARTS; k++) if (kartMs[k] && millis() - kartMs[k] < 10000) r.karts_mask |= (1 << (k - 1));
    r.uptime_min = (uint16_t)(millis() / 60000UL);
    delay(2);
    if (radio.transmit((uint8_t*)&r, sizeof r) == RADIOLIB_ERR_NONE) hbTx++;
    loraIrq = false; radio.startReceive();
  }
}

static void serviceUsb() {
  static char line[48]; static size_t n = 0;
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      line[n] = 0; n = 0; if (!line[0]) continue;
      char* arg = strchr(line, ' '); if (arg) { *arg++ = 0; while (*arg == ' ') arg++; }
      if (!strcmp(line, "oled") && arg) {
        char* ad = strchr(arg, ' '); if (ad) { *ad++ = 0; while (*ad == ' ') ad++; }
        if (strcmp(arg, "sh1106") && strcmp(arg, "ssd1306")) { logf("usage: oled sh1106|ssd1306 [3c|3d|auto]"); continue; }
        prefs.putString("oled", arg); prefs.putString("oledaddr", ad && *ad ? ad : "auto");
        logf("oled -> %s at %s, rebooting", arg, ad && *ad ? ad : "auto"); delay(200); ESP.restart();
      }
      else if (!strcmp(line, "status")) statMs = 0;
      else if (!strcmp(line, "reboot")) ESP.restart();
      else logf("commands: oled sh1106|ssd1306 [3c|3d|auto], status, reboot");
    } else if (n < sizeof line - 1) line[n++] = c;
  }
}

static void serviceStat() {
  if (millis() - statMs < 1000) return; statMs = millis();
  char karts[160] = ""; size_t n = 0;
  for (int k = 1; k <= GK_MAX_KARTS; k++) if (kartMs[k] && millis() - kartMs[k] < 10000) n += snprintf(karts + n, sizeof karts - n, "%s{\"kart\":%d,\"rssi\":%.0f,\"heard\":%lu,\"relayed\":%lu}", n ? "," : "", k, kartRssi[k], (unsigned long)heardKart[k], (unsigned long)relayedKart[k]);
  Serial.printf("{\"t\":\"stat\",\"fw\":\"%s\",\"radio\":%s,\"heard\":%lu,\"relayed\":%lu,\"hb_tx\":%lu,\"dupes\":%lu,\"dropped\":%lu,\"rx_bad\":%lu,\"pit_rssi\":%s,\"pit_age_s\":%s,\"batt_v\":%.2f,\"uptime_s\":%lu,\"oled\":%s,\"oled_drv\":\"%s\",\"oled_addr\":\"0x%02x\",\"id3c\":\"0x%02x\",\"i2c\":\"%s\",\"karts\":[%s]}\n",
                FW_VERSION, gkBool(radioOk), (unsigned long)heard, (unsigned long)relayed, (unsigned long)hbTx, (unsigned long)dupes, (unsigned long)dropped, (unsigned long)rxBad,
                baseHeardMs ? String((int)baseRssi).c_str() : "null", baseHeardMs ? String((unsigned long)((millis() - baseHeardMs) / 1000)).c_str() : "null",
                (pmuOk && pmu) ? pmu->getBattVoltage() / 1000.0 : 0.0, (unsigned long)(millis() / 1000), gkBool(oledOk), oledDrv.c_str(), oledAddr, id3c, i2cFound, karts);
}

static void serviceOled() {
  if (!oledOk || millis() - oledMs < 500) return; oledMs = millis();
  char l[32]; uint32_t now = millis();
  oled->clearBuffer(); oled->setFont(u8g2_font_7x14B_tf); oled->drawStr(0, 13, "GOKART REPEATER");
  oled->setFont(u8g2_font_6x10_tf);
  snprintf(l, sizeof l, "heard %lu relayed %lu", (unsigned long)heard, (unsigned long)relayed); oled->drawStr(0, 26, l);
  if (baseHeardMs) snprintf(l, sizeof l, "pit %.0fdBm %lus ago", baseRssi, (unsigned long)((now - baseHeardMs) / 1000)); else snprintf(l, sizeof l, "pit -- not heard");
  oled->drawStr(0, 37, l);
  int y = 48;
  for (int k = 1; k <= GK_MAX_KARTS && y <= 64; k++) if (kartMs[k] && now - kartMs[k] < 10000) { snprintf(l, sizeof l, "K%d %.0fdBm", k, kartRssi[k]); oled->drawStr(((k - 1) % 2) * 64, y, l); if ((k - 1) % 2) y += 11; }
  if (!radioOk) oled->drawStr(0, 64, "RADIO FAILED");
  oled->sendBuffer();
}

void setup() {
  Serial.begin(115200);
  pmuInit();
  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL); delay(50); i2cScan();
  prefs.begin("gkrep", false); oledDrv = prefs.getString("oled", "sh1106");   // LilyGo ships the Supreme with a 1.3" SH1106
  if (oledDrv == "ssd1306") oled = new U8G2_SSD1306_128X64_NONAME_F_HW_I2C(U8G2_R0, U8X8_PIN_NONE, PIN_OLED_SCL, PIN_OLED_SDA);
  else { oledDrv = "sh1106"; oled = new U8G2_SH1106_128X64_NONAME_F_HW_I2C(U8G2_R0, U8X8_PIN_NONE, PIN_OLED_SCL, PIN_OLED_SDA); }
  // Address: the QMC6310 magnetometer on this board can sit at 0x3C (chip id register 0x00 reads 0x80), so when
  // 0x3C is the compass, or only 0x3D answers, the screen is at 0x3D. `oled <drv> 3c|3d` overrides.
  id3c = strstr(i2cFound, "0x3c") ? i2cReadReg(0x3C, 0x00) : 0xFF;
  String a = prefs.getString("oledaddr", "auto");
  if (a == "3d") oledAddr = 0x3D; else if (a == "3c") oledAddr = 0x3C;
  else oledAddr = (strstr(i2cFound, "0x3d") && (!strstr(i2cFound, "0x3c") || id3c == 0x80)) ? 0x3D : 0x3C;
  oled->setI2CAddress(oledAddr << 1);
  oledOk = oled->begin();
  if (oledOk) { oled->clearBuffer(); oled->setFont(u8g2_font_7x14B_tf); oled->drawStr(0, 13, "GOKART REPEATER"); oled->setFont(u8g2_font_6x10_tf); oled->drawStr(0, 30, "starting..."); oled->sendBuffer(); }
  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_CS);
  radioStatus = radio.begin(GK_LORA_FREQ_MHZ, GK_LORA_BW_KHZ, GK_LORA_SF, GK_LORA_CR, GK_LORA_SYNC_WORD, LORA_TX_POWER_DBM, GK_LORA_PREAMBLE, LORA_TCXO_V, false);
  if (radioStatus == RADIOLIB_ERR_NONE) { radio.setDio2AsRfSwitch(true); radio.setCurrentLimit(140); radio.setPacketReceivedAction(onLoraIrq); radio.startReceive(); radioOk = true; }
  esp_task_wdt_config_t wdt = { .timeout_ms = WDT_TIMEOUT_S * 1000, .idle_core_mask = 0, .trigger_panic = true };
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
  esp_task_wdt_add(NULL);
  logf("boot %s (built " __DATE__ " " __TIME__ ") pmu %s radio %s (%d) oled %s i2c [%s]", FW_VERSION, pmuOk ? "ok" : "MISSING", radioOk ? "ok" : "FAILED", radioStatus, oledOk ? oledDrv.c_str() : "FAILED", i2cFound);
  logf("oled %s at 0x%02x (0x3c reg0 = 0x%02x%s)", oledDrv.c_str(), oledAddr, id3c, id3c == 0x80 ? " = QMC6310 compass" : "");
}

void loop() {
  esp_task_wdt_reset();
  serviceRx();
  serviceTx();
  serviceRx();
  serviceUsb();
  serviceStat();
  serviceOled();
  delay(1);
}
