/*
 * GoKart Control — pit base station firmware for LilyGo T-Beam S3 Core (ESP32-S3 + SX1262 + u-blox GNSS + AXP2101).
 *
 * A LoRa modem for the pit host (Raspberry Pi 4 or a laptop over USB):
 *   - receives every kart's telemetry packet and prints it as one JSON line per packet;
 *   - accepts JSON command lines from the host (mode / stop / start / ping) and transmits
 *     them, in the quiet tail of each second when GPS PPS is available;
 *   - prints its own GPS position and a status line once per second;
 *   - shows "karts online" and per-kart summaries on the OLED;
 *   - joins WiFi when credentials are stored and accepts ArduinoOTA firmware updates.
 *
 * Contract: firmware/base-tbeam-s3/SERIAL_PROTOCOL.md. Status: compiles; NOT yet run on hardware.
 */
#include <Arduino.h>
#include <Preferences.h>
#include <WiFi.h>
#include <ESPmDNS.h>
#include <ArduinoOTA.h>
#include <SPI.h>
#include <Wire.h>
#include <RadioLib.h>
#include <TinyGPSPlus.h>
#include <U8g2lib.h>
#include <ArduinoJson.h>
#include <XPowersLib.h>
#include <esp_task_wdt.h>
#include <GoKartProtocol.h>
#include "config.h"

// ---------------------------------------------------------------- settings
Preferences prefs;
String wifiSsid, wifiPass, otaPass;

// ---------------------------------------------------------------- pmu
XPowersLibInterface* pmu = nullptr;
bool pmuOk = false;

// ---------------------------------------------------------------- radio
SX1262 radio = new Module(PIN_LORA_CS, PIN_LORA_DIO1, PIN_LORA_RST, PIN_LORA_BUSY);
volatile bool loraIrq = false;
volatile uint32_t loraIrqMicros = 0;
void IRAM_ATTR onLoraIrq() { loraIrq = true; loraIrqMicros = micros(); }
bool     radioOk = false;
int      radioStatus = -999;
uint32_t loraTx = 0, loraRx = 0, loraRxBad = 0;
uint8_t  txSeq = 0;

// ---------------------------------------------------------------- gps
TinyGPSPlus gps;
HardwareSerial& gpsSerial = Serial1;
struct GpsCfg { uint32_t baud; bool swap; };
static const GpsCfg GPS_CFGS[] = { {9600,false}, {38400,false}, {115200,false}, {9600,true}, {38400,true}, {115200,true} };
static const int GPS_CFG_N = sizeof(GPS_CFGS) / sizeof(GPS_CFGS[0]);
int      gpsCfgIdx = 0;
bool     gpsLocked = false;
uint32_t gpsCfgStartMs = 0, gpsGoodAtStart = 0, gpsLastCharMs = 0;
volatile uint32_t ppsMicros = 0, ppsCount = 0;
void IRAM_ATTR onPps() { ppsMicros = micros(); ppsCount++; }
uint32_t lastFixMs = 0;
double   fixLat = 0, fixLon = 0;

// ---------------------------------------------------------------- fleet + commands
GkTelemetry fleet[GK_MAX_KARTS + 1];
uint32_t    fleetMs[GK_MAX_KARTS + 1];
float       fleetRssi[GK_MAX_KARTS + 1];
GkBenchFrame benchLast; uint32_t benchRx = 0, benchMs = 0; float benchRssi = 0;   // Colin's bench transmitter (kart id 0 = uncommissioned)
// Second-boundary estimate for the beacon and the command window: GPS PPS when available,
// else derived from kart slot timing (kart k uses its shared 50 ms slot after the edge), else free-running.
uint32_t edgeMicros = 0, edgeSetMs = 0; uint8_t edgeSrc = 0;
uint32_t beaconTx = 0, lastBeaconMicros = 0; uint8_t beaconSeq = 0;
struct QueuedCmd { GkCommandMsg msg; bool used; };
QueuedCmd cmdQueue[CMD_QUEUE_LEN];
int cmdHead = 0, cmdTail = 0;

// ---------------------------------------------------------------- display / misc
U8G2_SH1106_128X64_NONAME_F_HW_I2C oled(U8G2_R0, U8X8_PIN_NONE, PIN_OLED_SCL, PIN_OLED_SDA);
bool     oledOk = false;
uint32_t oledMs = 0, statMs = 0, wifiRetryMs = 0, hostLineMs = 0;
bool     wifiStarted = false, otaStarted = false, otaInProgress = false;
char     usbLine[300];
uint16_t usbLen = 0;
uint8_t  oledPage = 0;

// ---------------------------------------------------------------- helpers
static void logf(const char* fmt, ...) {
  char msg[200];
  va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
  for (char* p = msg; *p; p++) if (*p == '"' || *p == '\\') *p = '\'';
  Serial.printf("{\"t\":\"log\",\"msg\":\"%s\"}\n", msg);
}

static bool ppsFresh() { return ppsCount && (uint32_t)(micros() - ppsMicros) < 2000000UL; }

static uint32_t gpsUnix() {
  if (!gps.date.isValid() || !gps.time.isValid() || gps.date.age() > 5000) return 0;
  return gkUnixFromCivil(gps.date.year(), gps.date.month(), gps.date.day(), gps.time.hour(), gps.time.minute(), gps.time.second());
}

static bool ppsFreshB() { return ppsCount && (uint32_t)(micros() - ppsMicros) < 2000000UL; }
// microseconds into the current second, and which reference it is based on
static uint32_t basePhaseUs(uint8_t* src) {
  if (ppsFreshB()) { if (src) *src = 1; return (uint32_t)(micros() - ppsMicros) % 1000000UL; }
  if (edgeSetMs && millis() - edgeSetMs < 5000) { if (src) *src = 2; return (uint32_t)(micros() - edgeMicros) % 1000000UL; }
  if (src) *src = 0; return (micros() % 1000000UL);
}

static int kartsOnline() {
  int n = 0; uint32_t now = millis();
  for (int i = 1; i <= GK_MAX_KARTS; i++) if (fleetMs[i] && now - fleetMs[i] < KART_ONLINE_MS) n++;
  return n;
}

// ---------------------------------------------------------------- pmu
static void pmuInit() {
  Wire1.begin(PIN_PMU_SDA, PIN_PMU_SCL);
  pmu = new XPowersAXP2101(Wire1, PIN_PMU_SDA, PIN_PMU_SCL);
  pmuOk = pmu->init();
  if (!pmuOk) { delete pmu; pmu = nullptr; return; }
  // Rail map for the T-Beam S3 Core (Meshtastic Power.cpp): GNSS on ALDO4, LoRa on ALDO3,
  // M.2 on DCDC3, sensors/OLED on ALDO1, ALDO2 cannot be turned off, SD on BLDO1.
  pmu->setPowerChannelVoltage(XPOWERS_ALDO4, 3300); pmu->enablePowerOutput(XPOWERS_ALDO4);
  pmu->setPowerChannelVoltage(XPOWERS_ALDO3, 3300); pmu->enablePowerOutput(XPOWERS_ALDO3);
  pmu->setPowerChannelVoltage(XPOWERS_DCDC3, 3300); pmu->enablePowerOutput(XPOWERS_DCDC3);
  pmu->setPowerChannelVoltage(XPOWERS_ALDO2, 3300); pmu->enablePowerOutput(XPOWERS_ALDO2);
  pmu->setPowerChannelVoltage(XPOWERS_ALDO1, 3300); pmu->enablePowerOutput(XPOWERS_ALDO1);
  pmu->setPowerChannelVoltage(XPOWERS_BLDO1, 3300); pmu->enablePowerOutput(XPOWERS_BLDO1);
  pmu->setPowerChannelVoltage(XPOWERS_BLDO2, 3300); pmu->enablePowerOutput(XPOWERS_BLDO2);
  pmu->disablePowerOutput(XPOWERS_DCDC2);
  pmu->disablePowerOutput(XPOWERS_DCDC5);
  pmu->disableTSPinMeasure();                       // no thermistor on this board; leaving it on breaks charging
  pmu->setChargerConstantCurr(XPOWERS_AXP2101_CHG_CUR_500MA);
  pmu->setChargeTargetVoltage(XPOWERS_AXP2101_CHG_VOL_4V2);
  pmu->enableBattVoltageMeasure();
  pmu->enableVbusVoltageMeasure();
  pmu->enableSystemVoltageMeasure();
  pmu->disableIRQ(XPOWERS_AXP2101_ALL_IRQ);
  delay(100);                                      // let the rails settle before touching the radio
}

// ---------------------------------------------------------------- gps
static void gpsApplyCfg(int idx) {
  const GpsCfg& c = GPS_CFGS[idx];
  gpsSerial.end();
  gpsSerial.begin(c.baud, SERIAL_8N1, c.swap ? PIN_GNSS_TX : PIN_GNSS_RX, c.swap ? PIN_GNSS_RX : PIN_GNSS_TX);
  gpsCfgIdx = idx; gpsCfgStartMs = millis(); gpsGoodAtStart = gps.passedChecksum();
}

static void serviceGps() {
  while (gpsSerial.available()) { gps.encode(gpsSerial.read()); gpsLastCharMs = millis(); }
  uint32_t now = millis();
  if (!gpsLocked) {
    if (gps.passedChecksum() > gpsGoodAtStart + 2) { gpsLocked = true; logf("gps found: %lu baud%s", (unsigned long)GPS_CFGS[gpsCfgIdx].baud, GPS_CFGS[gpsCfgIdx].swap ? " (pins swapped)" : ""); }
    else if (now - gpsCfgStartMs > GPS_DETECT_STEP_MS) gpsApplyCfg((gpsCfgIdx + 1) % GPS_CFG_N);
  } else if (now - gpsLastCharMs > 15000) { gpsLocked = false; logf("gps silent, re-detecting"); gpsApplyCfg(gpsCfgIdx); }
  if (gps.location.isValid() && gps.location.isUpdated()) { fixLat = gps.location.lat(); fixLon = gps.location.lng(); lastFixMs = now; }
}

// ---------------------------------------------------------------- lora
static void serviceLoraRx() {
  if (!radioOk || !loraIrq) return;
  loraIrq = false;
  uint8_t buf[80];
  size_t len = radio.getPacketLength();
  if (len == 0 || len > sizeof buf) { radio.startReceive(); return; }
  int st = radio.readData(buf, len);
  float rssi = radio.getRSSI(), snr = radio.getSNR();
  radio.startReceive();
  if (st != RADIOLIB_ERR_NONE) { loraRxBad++; return; }
  // Historical 0.1.x bench images used a 47-byte 'GK' frame; current images use GkTelemetry.
  GkBenchFrame bf;
  if (gkDecodeBenchFrame(buf, len, bf)) {
    loraRx++; benchRx++; benchMs = millis(); benchRssi = rssi; benchLast = bf;
    if (bf.kartId >= 1 && bf.kartId <= GK_MAX_KARTS) {
      // Count it as that kart on the OLED; fill the fleet slot with what the frame carries.
      GkTelemetry& t = fleet[bf.kartId]; memset(&t, 0, sizeof t);
      gkFillHeader(t.h, GK_MSG_TELEMETRY, bf.kartId, GK_ADDR_ALL, (uint8_t)bf.seq);
      t.volts_x10 = (uint16_t)(bf.volts * 10); t.mode_req = GK_MODE_UNKNOWN; t.mode_actual = GK_MODE_UNKNOWN; t.scale_x1000 = 65535;
      t.flags = (bf.vescValid ? GK_FLAG_VESC_LINK : 0) | (bf.gpsValid ? GK_FLAG_GPS_FIX : 0) | ((bf.vescValid && bf.fault && bf.fault != 255) ? GK_FLAG_FAULT : 0);
      fleetMs[bf.kartId] = millis(); fleetRssi[bf.kartId] = rssi;
    }
    char out[700];
    gkBenchToJson(out, sizeof out, bf, rssi, snr, millis());
    Serial.println(out);
    return;
  }
  uint8_t type = gkValidate(buf, len);
  if (!type) { loraRxBad++; return; }
  loraRx++;
  const GkHeader* h = (const GkHeader*)buf;
  if (type == GK_MSG_TELEMETRY && h->src >= 1 && h->src <= GK_MAX_KARTS) {
    memcpy(&fleet[h->src], buf, len); fleetMs[h->src] = millis(); fleetRssi[h->src] = rssi;
    if (!ppsFreshB() && (fleet[h->src].flags & GK_FLAG_PPS)) {
      // Only PPS-tagged kart packets seed the base clock; random recovery-image TX cannot.
      // Shared 50 ms slots apply to both rates; a 2 Hz kart's second-round packet lands
      // 500 ms later, so ignore candidates that sit ~500 ms from the current estimate.
      uint32_t cand = loraIrqMicros - gkSlotOffsetUs(h->src) - GK_TELEMETRY_AIRTIME_US;
      bool accept = true;
      if (edgeSetMs && millis() - edgeSetMs < 5000) {
        int32_t delta = int32_t(cand-edgeMicros) % 1000000L;
        uint32_t d = delta < 0 ? delta+1000000L : delta;
        if (d > 440000UL && d < 560000UL) accept = false;
      }
      if (accept) { edgeMicros = cand; edgeSetMs = millis(); edgeSrc = 2; }
    }
    char out[640];
    gkTelemetryToJson(out, sizeof out, fleet[h->src], "tele", rssi, snr, millis());
    Serial.println(out);
  }
}

static bool queueCommand(const GkCommandMsg& m) {
  int next = (cmdTail + 1) % CMD_QUEUE_LEN;
  if (next == cmdHead) return false;
  cmdQueue[cmdTail].msg = m; cmdQueue[cmdTail].used = true; cmdTail = next;
  return true;
}

static void transmitCommand(const GkCommandMsg& m) {
  if (!radioOk) return;
  int st = radio.transmit((const uint8_t*)&m, sizeof m);
  loraIrq = false;
  radio.startReceive();
  if (st == RADIOLIB_ERR_NONE) loraTx++; else logf("lora tx error %d", st);
  Serial.printf("{\"t\":\"sent\",\"seq\":%u,\"cmd\":%u,\"kart\":%u,\"arg\":%u,\"ok\":%s}\n", m.h.seq, m.cmd, m.h.dst, m.arg, gkBool(st == RADIOLIB_ERR_NONE));
}

static void transmitBeacon(uint8_t src) {
  if (!radioOk) return;
  GkBaseInfo b; memset(&b, 0, sizeof b);
  gkFillHeader(b.h, GK_MSG_BASE_INFO, GK_ADDR_BASE, GK_ADDR_ALL, beaconSeq++);
  bool fix = lastFixMs && (millis() - lastFixMs) < 3000;
  if (fix) { b.lat_e7 = (int32_t)llround(fixLat * 1e7); b.lon_e7 = (int32_t)llround(fixLon * 1e7); }
  b.utc_s = gpsUnix(); b.sync_src = src; b.karts_online = (uint8_t)kartsOnline();
  b.phase_ms = (uint16_t)(basePhaseUs(nullptr) / 1000UL);
  int st = radio.transmit((const uint8_t*)&b, sizeof b);
  loraIrq = false; radio.startReceive();
  if (st == RADIOLIB_ERR_NONE) { loraTx++; beaconTx++; } else logf("beacon tx error %d", st);
}

static void serviceLoraTx() {
  uint8_t src; uint32_t phase = basePhaseUs(&src);
  // Once per second, in the tail after all kart slots: beacon first, then queued commands.
  bool inWindow = phase >= (uint32_t)GK_BEACON_PHASE_MS * 1000UL && phase < 985000UL;
  // One beacon per second: the edge estimate moves with every kart packet, so key on elapsed
  // time since the last beacon rather than on a second index derived from the edge.
  if (inWindow && phase+GK_BEACON_AIRTIME_US+1000UL <= 985000UL && (uint32_t)(micros() - lastBeaconMicros) > 600000UL) { lastBeaconMicros = micros(); transmitBeacon(src); }
  if (cmdHead == cmdTail) return;
  if (!inWindow) return; // Beacon clients also use the free-running base clock.
  while (cmdHead != cmdTail) {
    uint32_t currentPhase=basePhaseUs(nullptr);
    if (currentPhase < GK_BEACON_PHASE_MS*1000UL ||
        currentPhase+radio.getTimeOnAir(sizeof(GkCommandMsg))+1000UL > 985000UL) break;
    transmitCommand(cmdQueue[cmdHead].msg);
    cmdQueue[cmdHead].used = false;
    cmdHead = (cmdHead + 1) % CMD_QUEUE_LEN;
    if (src != 0 && basePhaseUs(nullptr) > 985000UL) break;
  }
}

// ---------------------------------------------------------------- host serial
static uint8_t cmdFromName(const char* s) {
  if (!strcmp(s, "mode"))  return GK_CMD_SET_MODE;
  if (!strcmp(s, "stop"))  return GK_CMD_STOP;
  if (!strcmp(s, "start")) return GK_CMD_START;
  if (!strcmp(s, "ping"))  return GK_CMD_PING;
  return GK_CMD_NONE;
}

static void hostJson(const char* line) {
  JsonDocument doc;
  if (deserializeJson(doc, line) != DeserializationError::Ok) { logf("bad json"); return; }
  const char* cmdName = doc["cmd"] | "";
  uint8_t cmd = cmdFromName(cmdName);
  if (!cmd) { logf("unknown cmd '%s'", cmdName); return; }
  int kart = doc["kart"] | 255;
  int seq  = doc["seq"]  | 0;
  int arg  = doc["arg"]  | 0;
  int ttl  = doc["ttl"]  | 0;
  if (!(kart == GK_ADDR_ALL || (kart >= 1 && kart <= GK_MAX_KARTS))) { logf("bad kart %d", kart); return; }
  if (seq < 1 || seq > 255) { logf("seq must be 1..255"); return; }
  if (cmd == GK_CMD_SET_MODE && (arg < 0 || arg >= GK_MODE_COUNT)) { logf("bad mode %d", arg); return; }
  GkCommandMsg m; memset(&m, 0, sizeof m);
  gkFillHeader(m.h, GK_MSG_COMMAND, GK_ADDR_BASE, (uint8_t)kart, (uint8_t)seq);
  m.cmd = cmd; m.arg = (uint8_t)arg; m.ttl_s = (uint16_t)ttl; m.issued_utc_s = gpsUnix();
  if (!queueCommand(m)) logf("command queue full, dropped seq %d", seq);
}

static void hostText(char* line) {
  char* arg = strchr(line, ' ');
  if (arg) { *arg++ = 0; while (*arg == ' ') arg++; }
  if (!strcmp(line, "status")) {
    Serial.printf("{\"t\":\"status\",\"fw\":\"%s\",\"radio\":%s,\"radio_status\":%d,\"pmu\":%s,\"gps_locked\":%s,\"gps_cfg\":\"%lu%s\",\"pps_count\":%lu,\"wifi\":\"%s\",\"ssid\":\"%s\",\"heap\":%lu}\n",
      FW_VERSION, gkBool(radioOk), radioStatus, gkBool(pmuOk), gkBool(gpsLocked), (unsigned long)GPS_CFGS[gpsCfgIdx].baud, GPS_CFGS[gpsCfgIdx].swap ? "s" : "",
      (unsigned long)ppsCount, WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : "", wifiSsid.c_str(), (unsigned long)ESP.getFreeHeap());
  }
  else if (!strcmp(line, "wifi") && arg) { char* pw = strchr(arg, ' '); if (pw) *pw++ = 0; prefs.putString("ssid", arg); prefs.putString("pass", pw ? pw : ""); logf("wifi credentials stored for '%s'; reboot to apply", arg); }
  else if (!strcmp(line, "otapw") && arg) { prefs.putString("otapw", arg); logf("ota password stored; reboot to apply"); }
  else if (!strcmp(line, "reboot")) { logf("rebooting"); delay(100); ESP.restart(); }
  else if (!strcmp(line, "help")) logf("json: {cmd:mode|stop|start|ping, kart:1-8|255, arg, seq:1-255, ttl}; text: status | wifi SSID PASS | otapw PASS | reboot");
  else logf("unknown command '%s'", line);
}

static void serviceHost() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      usbLine[usbLen] = 0;
      if (usbLen) { hostLineMs = millis(); if (usbLine[0] == '{') hostJson(usbLine); else hostText(usbLine); }
      usbLen = 0; continue;
    }
    if (usbLen < sizeof(usbLine) - 1) usbLine[usbLen++] = c;
  }
  if (millis() - statMs >= STAT_PERIOD_MS) {
    statMs = millis();
    bool fix = lastFixMs && (millis() - lastFixMs) < 3000;
    char lat[20] = "null", lon[20] = "null", utc[16] = "null";
    if (fix) { snprintf(lat, sizeof lat, "%.7f", fixLat); snprintf(lon, sizeof lon, "%.7f", fixLon); }
    uint32_t u = gpsUnix(); if (u) snprintf(utc, sizeof utc, "%lu", (unsigned long)u);
    Serial.printf("{\"t\":\"base\",\"lat\":%s,\"lon\":%s,\"fix\":%s,\"sats\":%u,\"utc\":%s,\"pps\":%s}\n",
                  lat, lon, gkBool(fix), gps.satellites.isValid() ? (unsigned)gps.satellites.value() : 0, utc, gkBool(ppsFresh()));
    char wifi[24] = "null";
    if (WiFi.status() == WL_CONNECTED) snprintf(wifi, sizeof wifi, "\"%s\"", WiFi.localIP().toString().c_str());
    char benchAge[12] = "null"; if (benchMs) snprintf(benchAge, sizeof benchAge, "%lu", (unsigned long)((millis() - benchMs) / 1000));
    Serial.printf("{\"t\":\"stat\",\"fw\":\"%s\",\"radio\":\"%s\",\"rx\":%lu,\"tx\":%lu,\"rx_bad\":%lu,\"uptime_s\":%lu,\"batt_v\":%.2f,\"wifi\":%s,\"karts_online\":%d,\"bench_rx\":%lu,\"bench_age_s\":%s,\"beacon_tx\":%lu,\"sync\":\"%s\"}\n",
                  FW_VERSION, radioOk ? "sx1262" : "none", (unsigned long)loraRx, (unsigned long)loraTx, (unsigned long)loraRxBad,
                  (unsigned long)(millis() / 1000), (pmuOk && pmu) ? pmu->getBattVoltage() / 1000.0 : 0.0, wifi, kartsOnline(),
                  (unsigned long)benchRx, benchAge, (unsigned long)beaconTx, ppsFreshB() ? "pps" : (edgeSetMs && millis() - edgeSetMs < 5000 ? "karts" : "free"));
  }
}

// ---------------------------------------------------------------- wifi / ota
static void wifiStart() {
  if (wifiSsid.length() == 0) return;
  WiFi.mode(WIFI_STA); WiFi.setSleep(false);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  wifiStarted = true;
}

static void serviceWifi() {
  if (!wifiStarted) return;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !otaStarted) {
    ArduinoOTA.setHostname("gokart-pit-radio");
    if (otaPass.length()) ArduinoOTA.setPassword(otaPass.c_str());
    ArduinoOTA.onStart([]() { otaInProgress = true; esp_task_wdt_delete(NULL); logf("ota start"); });
    ArduinoOTA.onEnd([]() { logf("ota done"); });
    ArduinoOTA.onError([](ota_error_t e) { otaInProgress = false; esp_task_wdt_add(NULL); logf("ota error %u", (unsigned)e); });
    ArduinoOTA.begin();
    otaStarted = true;
    logf("wifi %s ota host gokart-pit-radio.local", WiFi.localIP().toString().c_str());
  }
  if (otaStarted) ArduinoOTA.handle();
  if (!up && millis() - wifiRetryMs > 30000) { wifiRetryMs = millis(); WiFi.reconnect(); }
}

// ---------------------------------------------------------------- oled
static void serviceOled() {
  if (!oledOk || millis() - oledMs < OLED_REFRESH_MS) return;
  oledMs = millis();
  char l[32];
  uint32_t now = millis();
  oled.clearBuffer();
  oled.setFont(u8g2_font_6x10_tf);
  bool host = hostLineMs && now - hostLineMs < 15000;
  snprintf(l, sizeof l, "GOKART PIT %s%s", radioOk ? "" : "RADIO! ", host ? "host" : "");
  oled.drawStr(0, 9, l);
  oled.setFont(u8g2_font_7x14B_tf);
  snprintf(l, sizeof l, "Karts online %d/%d", kartsOnline(), GK_MAX_KARTS);
  oled.drawStr(0, 24, l);
  oled.setFont(u8g2_font_6x10_tf);
  // Up to three kart lines, cycling through the fleet every 2 s.
  int shown = 0, y = 35;
  static uint32_t pageMs = 0; if (now - pageMs > 2000) { pageMs = now; oledPage ^= 1; }
  for (int i = 1 + oledPage * 4; i <= GK_MAX_KARTS && shown < 3; i++) {
    if (!fleetMs[i]) continue;
    const GkTelemetry& t = fleet[i];
    uint32_t age = (now - fleetMs[i]) / 1000;
    const char* mode = (t.mode_actual != GK_MODE_UNKNOWN) ? gkModeName(t.mode_actual) : "----";
    snprintf(l, sizeof l, "K%d %-6.6s %4.1fV %3.0f %lus%s", i, (t.flags & GK_FLAG_STOPPED) ? "STOP" : mode, t.volts_x10 / 10.0, fleetRssi[i], (unsigned long)age, (t.flags & GK_FLAG_FAULT) ? "F" : "");
    oled.drawStr(0, y, l); y += 11; shown++;
  }
  if (!shown && benchMs && now - benchMs < 10000) { snprintf(l, sizeof l, "bench K%u %4.1fV %3.0fdBm %lus", benchLast.kartId, benchLast.vescValid ? benchLast.volts : 0.0f, benchRssi, (unsigned long)((now - benchMs) / 1000)); oled.drawStr(0, 35, l); shown = 1; }
  if (!shown) oled.drawStr(0, 35, "no karts heard yet");
  bool fix = lastFixMs && (now - lastFixMs) < 3000;
  snprintf(l, sizeof l, "GPS %s%u%s tx%lu rx%lu", fix ? "fix " : "", gps.satellites.isValid() ? (unsigned)gps.satellites.value() : 0, ppsFresh() ? "P" : "", (unsigned long)loraTx, (unsigned long)loraRx);
  oled.drawStr(0, 64, l);
  oled.sendBuffer();
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  pinMode(PIN_BUTTON, INPUT_PULLUP);
  prefs.begin("gokart", false);
  wifiSsid = prefs.getString("ssid", "");
  wifiPass = prefs.getString("pass", "");
  otaPass  = prefs.getString("otapw", "");

  pmuInit();

  Wire.begin(PIN_OLED_SDA, PIN_OLED_SCL);
  oledOk = oled.begin();
  if (oledOk) { oled.clearBuffer(); oled.setFont(u8g2_font_6x10_tf); oled.drawStr(0, 12, "GoKart pit base"); oled.drawStr(0, 24, FW_VERSION); oled.sendBuffer(); }

  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_CS);
  radioStatus = radio.begin(GK_LORA_FREQ_MHZ, GK_LORA_BW_KHZ, GK_LORA_SF, GK_LORA_CR, GK_LORA_SYNC_WORD, LORA_TX_POWER_DBM, GK_LORA_PREAMBLE, LORA_TCXO_V, false);
  if (radioStatus == RADIOLIB_ERR_NONE) {
    radio.setDio2AsRfSwitch(true);
    radio.setCurrentLimit(140);
    radio.setPacketReceivedAction(onLoraIrq);
    radio.startReceive();
    radioOk = true;
  }

  pinMode(PIN_GNSS_WAKE, OUTPUT); digitalWrite(PIN_GNSS_WAKE, HIGH);
  pinMode(PIN_GNSS_PPS, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_GNSS_PPS), onPps, RISING);
  gpsApplyCfg(0);

  wifiStart();

  esp_task_wdt_config_t wdt = { .timeout_ms = WDT_TIMEOUT_S * 1000, .idle_core_mask = 0, .trigger_panic = true };
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
  esp_task_wdt_add(NULL);

  memset(fleetMs, 0, sizeof fleetMs);
  logf("boot %s pmu %s radio %s (%d)", FW_VERSION, pmuOk ? "ok" : "MISSING", radioOk ? "ok" : "FAILED", radioStatus);
}

void loop() {
  if (!otaInProgress) esp_task_wdt_reset();
  serviceHost();
  serviceGps();
  serviceLoraRx();
  serviceLoraTx();
  serviceWifi();
  serviceOled();
  delay(1);
}
