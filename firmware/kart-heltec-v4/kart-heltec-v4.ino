/*
 * GoKart Control — kart node firmware for Heltec WiFi LoRa 32 V4.3 (ESP32-S3 + SX1262 + L76K).
 *
 * The Heltec is the kart supervisor:
 *   - talks to the VESC (wired UART by default, BLE optional), polls telemetry 5 Hz,
 *     reads the live limits back (read-only reconciliation) and applies mode / stop /
 *     start ONLY when a deliberate command arrives from the pit or over USB;
 *   - reads the L76K GPS and uses its PPS edge to transmit in its own LoRa time slot;
 *   - broadcasts one telemetry packet per second, hears the other karts and the pit;
 *   - prints JSON lines over USB for the (future) Raspberry Pi display and accepts
 *     maintenance commands on the same port;
 *   - shows status on the OLED;
 *   - joins WiFi when credentials are stored and accepts ArduinoOTA firmware updates.
 *
 * Reliability rule (docs/requirements.md): nothing in here writes to the VESC because a
 * link went down or came back. Boot and reconnect are read-only.
 *
 * Status: compiles; NOT yet run on hardware.
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
#include <esp_task_wdt.h>
#include <GoKartProtocol.h>
#include <VescLink.h>
#include "config.h"
#if !VESC_TRANSPORT_UART
#include <NimBLEDevice.h>
#endif

// ---------------------------------------------------------------- settings
Preferences prefs;
uint8_t kartId = DEFAULT_KART_ID;
String  wifiSsid, wifiPass, otaPass, vescMac;

// ---------------------------------------------------------------- radio
SX1262 radio = new Module(PIN_LORA_NSS, PIN_LORA_DIO1, PIN_LORA_RST, PIN_LORA_BUSY);
volatile bool loraIrq = false;
void IRAM_ATTR onLoraIrq() { loraIrq = true; }
bool     radioOk = false;
int      radioStatus = -999;
uint32_t loraTx = 0, loraRx = 0, loraRxBad = 0;
int8_t   baseRssi = 0;
uint32_t baseHeardMs = 0;
uint8_t  txSeq = 0;
uint32_t lastTxMs = 0, lastTxPps = 0;
uint16_t txJitter = 0;

// ---------------------------------------------------------------- gps
TinyGPSPlus gps;
HardwareSerial& gpsSerial = Serial1;
struct GpsCfg { uint32_t baud; bool swap; };
static const GpsCfg GPS_CFGS[] = { {9600,false}, {9600,true}, {115200,false}, {115200,true}, {38400,false}, {38400,true} };
static const int GPS_CFG_N = sizeof(GPS_CFGS) / sizeof(GPS_CFGS[0]);
int      gpsCfgIdx = 0;
bool     gpsLocked = false;
uint32_t gpsCfgStartMs = 0, gpsGoodAtStart = 0, gpsLastCharMs = 0;
volatile uint32_t ppsMicros = 0, ppsCount = 0;
void IRAM_ATTR onPps() { ppsMicros = micros(); ppsCount++; }
uint32_t lastFixMs = 0;
double   fixLat = 0, fixLon = 0;

// ---------------------------------------------------------------- vesc
VescLink vesc;
bool     vescLinkWas = false;
uint32_t vescPollMs = 0, vescMcconfMs = 0;

#if !VESC_TRANSPORT_UART
class BLEStream : public Stream {
  static const int BUF = 1024;
  uint8_t _buf[BUF];
  volatile int _head = 0, _tail = 0;
  NimBLERemoteCharacteristic* _tx = nullptr;
public:
  void clear() { _head = _tail = 0; }
  void setTx(NimBLERemoteCharacteristic* c) { _tx = c; }
  void push(const uint8_t* d, size_t n) { for (size_t i = 0; i < n; i++) { int nh = (_head + 1) % BUF; if (nh == _tail) return; _buf[_head] = d[i]; _head = nh; } }
  int available() override { return (_head - _tail + BUF) % BUF; }
  int peek() override { return (_head == _tail) ? -1 : _buf[_tail]; }
  int read() override { if (_head == _tail) return -1; uint8_t c = _buf[_tail]; _tail = (_tail + 1) % BUF; return c; }
  void flush() override {}
  size_t write(uint8_t c) override { return write(&c, 1); }
  size_t write(const uint8_t* b, size_t n) override {
    if (!_tx) return 0;
    // Respect the negotiated MTU: chunk writes at 180 bytes.
    size_t sent = 0;
    while (sent < n) { size_t k = min((size_t)180, n - sent); _tx->writeValue(b + sent, k, false); sent += k; }
    return n;
  }
};
BLEStream bleStream;
NimBLEClient* pClient = nullptr;
NimBLERemoteCharacteristic *pTxChar = nullptr, *pRxChar = nullptr;
volatile bool bleConnected = false;
volatile bool bleScanning = false;
uint32_t bleScanMs = 0, bleConnectedMs = 0;
struct BleCand { NimBLEAddress addr; int rssi; uint32_t seenMs; };
BleCand bleCands[6]; int bleCandN = 0;
bool bleWantConnect = false; NimBLEAddress bleTarget;
static const char* NUS_SERVICE = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* NUS_TX      = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* NUS_RX      = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E";
#endif

// ---------------------------------------------------------------- supervisory state
uint8_t  modeReq = GK_MODE_UNKNOWN;
bool     stopActive = false;
float    preStopScale = -1.0f;
uint8_t  lastCmdSeq = 0, lastCmdSrc = 0xFE, cmdResult = GK_RES_NONE;
// Per-source duplicate detection: a retransmit (same source, same seq, same issue time)
// is reported again but never re-applied. Index 0 = pit base, 1 = USB/local.
uint8_t  dupSeq[2] = {0, 0};
uint32_t dupIssued[2] = {0, 0};
bool     dupValid[2] = {false, false};
bool     modeConfirmed = false;
enum WriteState { W_IDLE, W_SENT, W_VERIFY };
WriteState wState = W_IDLE;
float    wTarget = 0;
uint32_t wSentMs = 0, wAckSeen = 0;
int      rampIdx = -1;
uint32_t rampNextMs = 0;
uint8_t  usbSeq = 0;

// ---------------------------------------------------------------- fleet
GkTelemetry fleet[GK_MAX_KARTS + 1];
uint32_t    fleetMs[GK_MAX_KARTS + 1];

// ---------------------------------------------------------------- display / misc
U8G2_SSD1306_128X64_NONAME_F_HW_I2C oled(U8G2_R0, PIN_OLED_RST, PIN_OLED_SCL, PIN_OLED_SDA);
bool     oledOk = false;
uint32_t oledMs = 0, usbReportMs = 0, wifiRetryMs = 0;
bool     wifiStarted = false, otaStarted = false, otaInProgress = false;
char     usbLine[200];
uint8_t  usbLen = 0;

// ---------------------------------------------------------------- helpers
static void logf(const char* fmt, ...) {
  char msg[200];
  va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
  for (char* p = msg; *p; p++) if (*p == '"' || *p == '\\') *p = '\'';
  Serial.printf("{\"t\":\"log\",\"kart\":%u,\"msg\":\"%s\"}\n", kartId, msg);
}

static bool writesEnabled() {
#if ENABLE_VESC_WRITES
  return true;
#else
  return false;
#endif
}

static bool vescUp() { return vesc.linkUp(VESC_LINK_TIMEOUT_MS); }

static uint32_t gpsUnix() {
  if (!gps.date.isValid() || !gps.time.isValid() || gps.date.age() > 5000) return 0;
  return gkUnixFromCivil(gps.date.year(), gps.date.month(), gps.date.day(), gps.time.hour(), gps.time.minute(), gps.time.second());
}

static bool ppsFresh() {
  if (!ppsCount) return false;
  return (uint32_t)(micros() - ppsMicros) < 2000000UL;
}

static uint8_t modeActual(float* scaleOut) {
  const VescActiveLimits& L = vesc.limits();
  if (!L.receivedMs || !vescUp()) { if (scaleOut) *scaleOut = -1; return GK_MODE_UNKNOWN; }
  if (scaleOut) *scaleOut = L.currentMaxScale;
  for (uint8_t i = 0; i < GK_MODE_COUNT; i++)
    if (fabsf(L.currentMaxScale - MODES[i].currentMaxScale) < MODE_MATCH_TOL) return i;
  return GK_MODE_UNKNOWN;
}

// ---------------------------------------------------------------- telemetry
static void fillTelemetry(GkTelemetry& t) {
  memset(&t, 0, sizeof t);
  gkFillHeader(t.h, GK_MSG_TELEMETRY, kartId, GK_ADDR_ALL, txSeq);
  uint32_t now = millis();
  bool fix = lastFixMs && (now - lastFixMs) < 3000 && gps.location.isValid();
  if (fix) { t.lat_e7 = (int32_t)llround(fixLat * 1e7); t.lon_e7 = (int32_t)llround(fixLon * 1e7); }
  t.gps_kph_x10 = gps.speed.isValid() ? (uint16_t)min(65535.0, gps.speed.kmph() * 10.0) : 0;
  t.heading_x10 = gps.course.isValid() ? (uint16_t)(gps.course.deg() * 10.0) : 0;
  t.sats = gps.satellites.isValid() ? (uint8_t)min((uint32_t)255, (uint32_t)gps.satellites.value()) : 0;
  t.gps_age_s = lastFixMs ? (uint8_t)min(255UL, (unsigned long)((now - lastFixMs) / 1000)) : 255;
  const VescValues& v = vesc.values();
  bool vup = vescUp();
  if (vup) {
    t.volts_x10 = (uint16_t)(v.inputVoltage * 10);
    t.motor_a_x10 = (int16_t)(v.motorCurrent * 10);
    t.batt_a_x10 = (int16_t)(v.inputCurrent * 10);
    t.erpm = v.erpm;
    t.duty_pct = (int8_t)(v.duty * 100);
    t.esc_temp_x10 = (int16_t)(v.tempMosfet * 10);
    t.fault = v.faultCode;
    t.ah_x100 = (uint16_t)min(65535.0f, v.ampHours * 100);
  }
  float scale; uint8_t ma = modeActual(&scale);
  t.mode_req = modeReq;
  t.mode_actual = ma;
  t.scale_x1000 = (scale < 0) ? 65535 : (uint16_t)(scale * 1000);
  t.last_cmd_seq = lastCmdSeq;
  t.cmd_result = cmdResult;
  t.uptime_s = (uint16_t)(now / 1000);
  t.base_rssi = (baseHeardMs && now - baseHeardMs < 30000) ? baseRssi : 0;
  t.flags = (fix ? GK_FLAG_GPS_FIX : 0) | (ppsFresh() ? GK_FLAG_PPS : 0) | (vup ? GK_FLAG_VESC_LINK : 0)
          | (stopActive ? GK_FLAG_STOPPED : 0) | (modeConfirmed ? GK_FLAG_MODE_CONFIRMED : 0)
          | ((vup && v.faultCode) ? GK_FLAG_FAULT : 0) | (WiFi.status() == WL_CONNECTED ? GK_FLAG_WIFI : 0)
          | (writesEnabled() ? GK_FLAG_WRITES_ENABLED : 0);
}

// ---------------------------------------------------------------- vesc writes
static bool startWrite(float scale) {
  if (!writesEnabled() || !vescUp()) return false;
  VescTempLimits l;
  l.currentMinScale = BASE_CURRENT_MIN_SCALE;
  l.currentMaxScale = scale;
  l.minErpm = BASE_MIN_ERPM; l.maxErpm = BASE_MAX_ERPM;
  l.minDuty = BASE_MIN_DUTY; l.maxDuty = BASE_MAX_DUTY;
  l.wattMin = BASE_WATT_MIN; l.wattMax = BASE_WATT_MAX;
  l.sendInCurrent = false;
  wAckSeen = vesc.lastAckMs();
  vesc.sendTempLimits(l);
  wState = W_SENT; wTarget = scale; wSentMs = millis();
  logf("vesc write scale %.3f", scale);
  return true;
}

static void serviceWrites() {
  uint32_t now = millis();
  if (rampIdx >= 0 && wState == W_IDLE && (int32_t)(now - rampNextMs) >= 0) {
    const int n = sizeof(STOP_RAMP) / sizeof(STOP_RAMP[0]);
    if (!startWrite(STOP_RAMP[rampIdx])) { cmdResult = GK_RES_NO_VESC; rampIdx = -1; }
    else { rampIdx++; rampNextMs = now + STOP_RAMP_STEP_MS; if (rampIdx >= n) rampIdx = -1; }
  }
  switch (wState) {
    case W_IDLE: break;
    case W_SENT:
      if (vesc.lastAckMs() != wAckSeen) { wState = W_VERIFY; wSentMs = now; vesc.requestMotorConfig(); }
      else if (now - wSentMs > VESC_ACK_TIMEOUT_MS) { wState = W_IDLE; if (rampIdx < 0) cmdResult = GK_RES_UNKNOWN; logf("vesc write: no ack"); }
      break;
    case W_VERIFY:
      if (vesc.limits().receivedMs && (int32_t)(vesc.limits().receivedMs - wSentMs) >= 0) {
        wState = W_IDLE;
        bool ok = fabsf(vesc.limits().currentMaxScale - wTarget) < MODE_MATCH_TOL;
        if (rampIdx < 0) cmdResult = ok ? GK_RES_APPLIED : GK_RES_UNKNOWN;
        modeConfirmed = ok && !stopActive && modeReq != GK_MODE_UNKNOWN && fabsf(wTarget - MODES[modeReq].currentMaxScale) < MODE_MATCH_TOL;
        logf("vesc write verify %s (scale %.3f)", ok ? "ok" : "MISMATCH", vesc.limits().currentMaxScale);
      } else if (now - wSentMs > VESC_ACK_TIMEOUT_MS) { wState = W_IDLE; if (rampIdx < 0) cmdResult = GK_RES_UNKNOWN; }
      break;
  }
}

// ---------------------------------------------------------------- commands
static void emitCmd(uint8_t src, uint8_t seq, uint8_t cmd, uint8_t arg) {
  Serial.printf("{\"t\":\"cmd\",\"kart\":%u,\"src\":%u,\"seq\":%u,\"cmd\":%u,\"arg\":%u,\"result\":%u}\n", kartId, src, seq, cmd, arg, cmdResult);
}

static void handleCommand(uint8_t src, uint8_t seq, uint8_t cmd, uint8_t arg, uint16_t ttl, uint32_t issued) {
  int si = (src == GK_ADDR_BASE) ? 0 : 1;
  if (dupValid[si] && dupSeq[si] == seq && dupIssued[si] == issued) { emitCmd(src, seq, cmd, arg); return; }   // retransmit: report, do not re-apply
  dupValid[si] = true; dupSeq[si] = seq; dupIssued[si] = issued;
  lastCmdSeq = seq; lastCmdSrc = src;
  if (ttl && issued) {
    uint32_t now = gpsUnix();
    if (now && now > issued + ttl) { cmdResult = GK_RES_REJECTED; logf("command %u expired", seq); emitCmd(src, seq, cmd, arg); return; }
  }
  cmdResult = GK_RES_ACCEPTED;
  switch (cmd) {
    case GK_CMD_PING:
      cmdResult = GK_RES_APPLIED; break;
    case GK_CMD_SET_MODE:
      if (arg >= GK_MODE_COUNT) { cmdResult = GK_RES_REJECTED; break; }
      modeReq = arg; modeConfirmed = false;
      if (!writesEnabled()) { cmdResult = GK_RES_REJECTED; break; }
      if (stopActive) break;                             // remembered; applied at START
      if (!startWrite(MODES[arg].currentMaxScale)) cmdResult = GK_RES_NO_VESC;
      break;
    case GK_CMD_STOP:
      if (!writesEnabled()) { cmdResult = GK_RES_REJECTED; break; }
      if (!vescUp()) { cmdResult = GK_RES_NO_VESC; break; }
      if (!stopActive) preStopScale = vesc.limits().receivedMs ? vesc.limits().currentMaxScale : -1.0f;
      stopActive = true; modeConfirmed = false;
      rampIdx = 0; rampNextMs = millis();
      break;
    case GK_CMD_START: {
      if (!writesEnabled()) { cmdResult = GK_RES_REJECTED; break; }
      stopActive = false; rampIdx = -1;
      float s = (modeReq != GK_MODE_UNKNOWN) ? MODES[modeReq].currentMaxScale : preStopScale;
      if (s < 0) { cmdResult = GK_RES_REJECTED; logf("start: no mode to restore"); break; }
      if (!startWrite(s)) cmdResult = GK_RES_NO_VESC;
      break;
    }
    default:
      cmdResult = GK_RES_REJECTED;
  }
  emitCmd(src, seq, cmd, arg);
}

// ---------------------------------------------------------------- vesc link
#if !VESC_TRANSPORT_UART
static void onNotify(NimBLERemoteCharacteristic*, uint8_t* d, size_t n, bool) { bleStream.push(d, n); }
class ClientCB : public NimBLEClientCallbacks {
  void onDisconnect(NimBLEClient*, int reason) override { bleConnected = false; }
};
static bool sameMac(const NimBLEAddress& a, const String& macStr) {
  String x = String(a.toString().c_str()); x.toLowerCase(); String y = macStr; y.toLowerCase(); y.trim();
  return x == y;
}
class ScanCB : public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice* d) override {
    if (d->getName() != VESC_BLE_NAME) return;
    for (int i = 0; i < bleCandN; i++) if (bleCands[i].addr == d->getAddress()) { bleCands[i].rssi = d->getRSSI(); bleCands[i].seenMs = millis(); return; }
    if (bleCandN < 6) { bleCands[bleCandN].addr = d->getAddress(); bleCands[bleCandN].rssi = d->getRSSI(); bleCands[bleCandN].seenMs = millis(); bleCandN++; }
  }
  void onScanEnd(const NimBLEScanResults&, int) override { bleScanning = false; }
};
static ScanCB scanCB;
static void bleReport() {
  Serial.printf("{\"t\":\"ble\",\"kart\":%u,\"pinned\":\"%s\",\"connected\":%s,\"candidates\":[", kartId, vescMac.c_str(), gkBool(bleConnected));
  for (int i = 0; i < bleCandN; i++) Serial.printf("%s{\"addr\":\"%s\",\"rssi\":%d,\"age_s\":%lu}", i ? "," : "", bleCands[i].addr.toString().c_str(), bleCands[i].rssi, (unsigned long)((millis() - bleCands[i].seenMs) / 1000));
  Serial.println("]}");
}
static void bleStartScan() {
  NimBLEScan* scan = NimBLEDevice::getScan();
  scan->setScanCallbacks(&scanCB, false);
  scan->setActiveScan(true); scan->setInterval(100); scan->setWindow(60);
  scan->setDuplicateFilter(false);
  bleCandN = 0;
  bleScanning = scan->start(BLE_SCAN_MS, false, true);
  bleScanMs = millis();
}
static bool bleConnectTo(const NimBLEAddress& target) {
  if (!pClient) { pClient = NimBLEDevice::createClient(); pClient->setClientCallbacks(new ClientCB(), false); pClient->setConnectionParams(12, 12, 0, 200); pClient->setConnectTimeout(4000); }
  if (!pClient->connect(target)) { logf("ble connect failed %s", target.toString().c_str()); return false; }
  NimBLERemoteService* svc = pClient->getService(NUS_SERVICE);
  if (!svc) { pClient->disconnect(); logf("ble: no NUS service"); return false; }
  pTxChar = svc->getCharacteristic(NUS_TX);
  pRxChar = svc->getCharacteristic(NUS_RX);
  if (!pTxChar || !pRxChar) { pClient->disconnect(); logf("ble: NUS characteristics missing"); return false; }
  if (pRxChar->canNotify()) pRxChar->subscribe(true, onNotify);
  bleStream.clear(); bleStream.setTx(pTxChar);
  vesc.setPort(&bleStream);
  bleConnected = true; bleConnectedMs = millis();
  logf("ble connected %s", target.toString().c_str());
  return true;
}
static void bleService() {
  if (bleConnected) return;
  uint32_t now = millis();
  if (bleScanning) return;
  // A scan just finished (or never ran): decide.
  if (bleScanMs && now - bleScanMs < BLE_SCAN_EVERY_MS) {
    if (bleWantConnect) {
      bleWantConnect = false;
      if (!bleConnectTo(bleTarget)) vesc.markLinkLost();
    }
    return;
  }
  if (bleScanMs) {                                   // previous scan results are in: report and pick
    bleReport();
    if (vescMac.length() >= 17) {
      for (int i = 0; i < bleCandN; i++) if (sameMac(bleCands[i].addr, vescMac)) { bleTarget = bleCands[i].addr; bleWantConnect = true; break; }
      if (!bleWantConnect && bleCandN) logf("ble: pinned VESC %s not seen (%d other VESC in range)", vescMac.c_str(), bleCandN);
    } else if (bleCandN) {
      logf("ble: %d VESC in range, not connecting until pinned with vescmac", bleCandN);
    }
    if (bleWantConnect) { if (!bleConnectTo(bleTarget)) vesc.markLinkLost(); bleWantConnect = false; return; }
  }
  bleStartScan();
}
#endif

static void serviceVesc() {
#if !VESC_TRANSPORT_UART
  bleService();
  if (!bleConnected) { if (vescLinkWas) { vescLinkWas = false; modeConfirmed = false; logf("vesc link lost"); } return; }
#endif
  vesc.poll();
  uint32_t now = millis();
  bool up = vescUp();
  if (up && !vescLinkWas) {              // link came up: read-only reconciliation only
    vesc.requestFirmware();
    vesc.requestMotorConfig(); vescMcconfMs = now;
    modeConfirmed = false;
    logf("vesc link up");
  }
  if (!up && vescLinkWas) { modeConfirmed = false; logf("vesc link lost"); }
  vescLinkWas = up;
  if (now - vescPollMs >= VESC_POLL_MS) { vescPollMs = now; vesc.requestValues(); }
  if (up && now - vescMcconfMs >= VESC_MCCONF_REFRESH_MS && wState == W_IDLE) { vescMcconfMs = now; vesc.requestMotorConfig(); }
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
static void emitFleet(uint8_t id, float rssi, float snr) {
  char buf[640];
  gkTelemetryToJson(buf, sizeof buf, fleet[id], "fleet", rssi, snr, millis());
  Serial.println(buf);
}

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
  uint8_t type = gkValidate(buf, len);
  if (!type) { loraRxBad++; return; }
  loraRx++;
  const GkHeader* h = (const GkHeader*)buf;
  if (h->src == GK_ADDR_BASE) { baseRssi = (int8_t)rssi; baseHeardMs = millis(); }
  switch (type) {
    case GK_MSG_TELEMETRY:
      if (h->src >= 1 && h->src <= GK_MAX_KARTS && h->src != kartId) { memcpy(&fleet[h->src], buf, len); fleetMs[h->src] = millis(); emitFleet(h->src, rssi, snr); }
      break;
    case GK_MSG_COMMAND: {
      GkCommandMsg c; memcpy(&c, buf, len);
      if (gkAddressedToMe(c.h, kartId)) handleCommand(c.h.src, c.h.seq, c.cmd, c.arg, c.ttl_s, c.issued_utc_s);
      break;
    }
    default: break;
  }
}

static bool slotDue() {
  uint32_t now = millis();
  if (ppsFresh()) {
    noInterrupts(); uint32_t pc = ppsCount, pm = ppsMicros; interrupts();
    if (lastTxPps == pc) return false;
    int32_t since = (int32_t)(micros() - pm);
    int32_t start = (int32_t)(kartId - 1) * GK_SLOT_MS * 1000 - GK_SLOT_GUARD_MS * 1000;
    if (since >= start && since < start + 60000) { lastTxPps = pc; return true; }
    return false;
  }
  return (now - lastTxMs) >= (uint32_t)(GK_REPORT_PERIOD_MS + txJitter);
}

static void serviceLoraTx() {
  if (!radioOk || !slotDue()) return;
  GkTelemetry t; fillTelemetry(t);
  digitalWrite(PIN_LED, HIGH);
  int st = radio.transmit((uint8_t*)&t, sizeof t);
  loraIrq = false;                       // the TX-done interrupt also raised DIO1
  radio.startReceive();
  digitalWrite(PIN_LED, LOW);
  lastTxMs = millis(); txJitter = random(0, 150); txSeq++;
  if (st == RADIOLIB_ERR_NONE) loraTx++; else logf("lora tx error %d", st);
}

// ---------------------------------------------------------------- wifi / ota
static void wifiStart() {
  if (wifiSsid.length() == 0) return;
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(wifiSsid.c_str(), wifiPass.c_str());
  wifiStarted = true;
}

static void serviceWifi() {
  if (!wifiStarted) return;
  bool up = WiFi.status() == WL_CONNECTED;
  if (up && !otaStarted) {
    char host[20]; snprintf(host, sizeof host, "gokart-%u", kartId);
    ArduinoOTA.setHostname(host);
    if (otaPass.length()) ArduinoOTA.setPassword(otaPass.c_str());
    ArduinoOTA.onStart([]() { otaInProgress = true; esp_task_wdt_delete(NULL); logf("ota start"); });
    ArduinoOTA.onEnd([]() { logf("ota done"); });
    ArduinoOTA.onError([](ota_error_t e) { otaInProgress = false; esp_task_wdt_add(NULL); logf("ota error %u", (unsigned)e); });
    ArduinoOTA.begin();
    otaStarted = true;
    logf("wifi %s ota host %s.local", WiFi.localIP().toString().c_str(), host);
  }
  if (otaStarted) ArduinoOTA.handle();
  if (!up && millis() - wifiRetryMs > 30000) { wifiRetryMs = millis(); WiFi.reconnect(); }
}

// ---------------------------------------------------------------- usb
static void usbReport() {
  GkTelemetry t; fillTelemetry(t);
  char buf[640];
  gkTelemetryToJson(buf, sizeof buf, t, "self", 0, 0, millis());
  Serial.println(buf);
}

static void usbStatus() {
  const VescActiveLimits& L = vesc.limits();
  Serial.printf("{\"t\":\"status\",\"kart\":%u,\"fw\":\"%s\",\"writes\":%s,\"transport\":\"%s\",\"radio\":%s,\"radio_status\":%d,"
                "\"lora_tx\":%lu,\"lora_rx\":%lu,\"lora_rx_bad\":%lu,\"gps_locked\":%s,\"gps_cfg\":\"%lu%s\",\"pps_count\":%lu,"
                "\"vesc_fw\":\"%u.%02u\",\"vesc_hw\":\"%s\",\"vesc_packets\":%lu,\"vesc_crc_err\":%lu,\"vesc_limits\":%s,"
                "\"l_current_max\":%.1f,\"l_in_current_max\":%.1f,\"scale_max\":%.3f,\"scale_min\":%.3f,\"max_erpm\":%.0f,\"max_duty\":%.3f,"
                "\"poles\":%u,\"gear\":%.4f,\"wheel_m\":%.4f,\"wifi\":\"%s\",\"ssid\":\"%s\",\"vescmac\":\"%s\",\"heap\":%lu}\n",
                kartId, FW_VERSION, gkBool(writesEnabled()), VESC_TRANSPORT_UART ? "uart" : "ble", gkBool(radioOk), radioStatus,
                (unsigned long)loraTx, (unsigned long)loraRx, (unsigned long)loraRxBad, gkBool(gpsLocked),
                (unsigned long)GPS_CFGS[gpsCfgIdx].baud, GPS_CFGS[gpsCfgIdx].swap ? "s" : "", (unsigned long)ppsCount,
                vesc.fwMajor, vesc.fwMinor, vesc.hwName, (unsigned long)vesc.packets, (unsigned long)vesc.crcErrors, gkBool(L.receivedMs != 0),
                L.currentMax, L.inCurrentMax, L.currentMaxScale, L.currentMinScale, L.maxErpm, L.maxDuty,
                L.motorPoles, L.gearRatio, L.wheelDiameter,
                WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : "", wifiSsid.c_str(), vescMac.c_str(),
                (unsigned long)ESP.getFreeHeap());
}

static void usbCommand(char* line) {
  while (*line == ' ') line++;
  char* arg = strchr(line, ' ');
  if (arg) { *arg++ = 0; while (*arg == ' ') arg++; }
  if (!strcmp(line, "status")) usbStatus();
  else if (!strcmp(line, "id") && arg) { int n = atoi(arg); if (n >= 1 && n <= GK_MAX_KARTS) { kartId = n; prefs.putUChar("id", kartId); logf("kart id %u stored; reboot to rename OTA host", kartId); } else logf("id must be 1..8"); }
  else if (!strcmp(line, "mode") && arg) handleCommand(0xFE, ++usbSeq, GK_CMD_SET_MODE, atoi(arg), 0, 0);
  else if (!strcmp(line, "stop"))  handleCommand(0xFE, ++usbSeq, GK_CMD_STOP, 0, 0, 0);
  else if (!strcmp(line, "start")) handleCommand(0xFE, ++usbSeq, GK_CMD_START, 0, 0, 0);
  else if (!strcmp(line, "ping"))  handleCommand(0xFE, ++usbSeq, GK_CMD_PING, 0, 0, 0);
  else if (!strcmp(line, "wifi") && arg) {
    char* pw = strchr(arg, ' '); if (pw) { *pw++ = 0; }
    prefs.putString("ssid", arg); prefs.putString("pass", pw ? pw : "");
    logf("wifi credentials stored for '%s'; reboot to apply", arg);
  }
  else if (!strcmp(line, "otapw") && arg) { prefs.putString("otapw", arg); logf("ota password stored; reboot to apply"); }
  else if (!strcmp(line, "vescmac") && arg) {
    if (!strcmp(arg, "clear")) { prefs.remove("vescmac"); vescMac = ""; logf("vesc address cleared"); }
    else { prefs.putString("vescmac", arg); vescMac = arg; logf("vesc address pinned to %s", arg); }
#if !VESC_TRANSPORT_UART
    if (bleConnected && pClient) pClient->disconnect();
#endif
  }
#if !VESC_TRANSPORT_UART
  else if (!strcmp(line, "blescan")) { bleReport(); if (!bleConnected && !bleScanning) { bleScanMs = 0; } }
#endif
  else if (!strcmp(line, "reboot")) { logf("rebooting"); delay(100); ESP.restart(); }
  else if (!strcmp(line, "help")) logf("commands: status | id N | mode 0..2 | stop | start | ping | wifi SSID PASS | otapw PASS | vescmac XX:XX:XX:XX:XX:XX | vescmac clear | blescan | reboot");
  else logf("unknown command '%s'", line);
}

static void serviceUsb() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') { usbLine[usbLen] = 0; if (usbLen) usbCommand(usbLine); usbLen = 0; continue; }
    if (usbLen < sizeof(usbLine) - 1) usbLine[usbLen++] = c;
  }
  if (millis() - usbReportMs >= USB_SELF_REPORT_MS) { usbReportMs = millis(); usbReport(); }
}

// ---------------------------------------------------------------- oled
static void serviceOled() {
  if (!oledOk || millis() - oledMs < OLED_REFRESH_MS) return;
  oledMs = millis();
  char l[32];
  float scale; uint8_t ma = modeActual(&scale);
  oled.clearBuffer();
  oled.setFont(u8g2_font_6x10_tf);
  snprintf(l, sizeof l, "K%u %s%s", kartId, ma != GK_MODE_UNKNOWN ? MODES[ma].name : (modeReq != GK_MODE_UNKNOWN ? MODES[modeReq].name : "----"), modeConfirmed ? "*" : "");
  oled.drawStr(0, 9, l);
  if (stopActive) { oled.drawBox(94, 0, 34, 11); oled.setDrawColor(0); oled.drawStr(98, 9, "STOP"); oled.setDrawColor(1); }
  else if (!writesEnabled()) oled.drawStr(100, 9, "RO");
  const VescValues& v = vesc.values();
  if (vescUp()) snprintf(l, sizeof l, "VESC %.1fV %.0fA %.0fC%s", v.inputVoltage, v.inputCurrent, v.tempMosfet, v.faultCode ? " F" : "");
#if !VESC_TRANSPORT_UART
  else if (bleConnected) snprintf(l, sizeof l, "VESC ble up, no data");
  else if (vescMac.length() < 17) snprintf(l, sizeof l, "BLE %d VESC seen, pin!", bleCandN);
  else snprintf(l, sizeof l, "BLE %s %s", bleScanning ? "scan" : "wait", vescMac.substring(9).c_str());
#else
  else snprintf(l, sizeof l, "VESC -- no link");
#endif
  oled.drawStr(0, 20, l);
  bool fix = lastFixMs && (millis() - lastFixMs) < 3000;
  snprintf(l, sizeof l, "GPS %s%u sat %.0fmph%s", gpsLocked ? "" : "?", gps.satellites.isValid() ? (unsigned)gps.satellites.value() : 0, fix ? gps.speed.mph() : 0.0, ppsFresh() ? " P" : "");
  oled.drawStr(0, 31, l);
  snprintf(l, sizeof l, "LoRa%s tx%lu rx%lu", radioOk ? "" : "!", (unsigned long)loraTx, (unsigned long)loraRx);
  oled.drawStr(0, 42, l);
  if (baseHeardMs) snprintf(l, sizeof l, "pit %ddBm %lus ago", baseRssi, (unsigned long)((millis() - baseHeardMs) / 1000));
  else snprintf(l, sizeof l, "pit -- not heard");
  oled.drawStr(0, 53, l);
  if (WiFi.status() == WL_CONNECTED) snprintf(l, sizeof l, "wifi %s", WiFi.localIP().toString().c_str());
  else snprintf(l, sizeof l, "wifi %s", wifiStarted ? "..." : "off");
  oled.drawStr(0, 64, l);
  oled.sendBuffer();
}

// ---------------------------------------------------------------- setup / loop
void setup() {
  Serial.begin(115200);
  pinMode(PIN_LED, OUTPUT); digitalWrite(PIN_LED, LOW);
  pinMode(PIN_BUTTON, INPUT_PULLUP);

  prefs.begin("gokart", false);
  kartId   = prefs.getUChar("id", DEFAULT_KART_ID);
  wifiSsid = prefs.getString("ssid", "");
  wifiPass = prefs.getString("pass", "");
  otaPass  = prefs.getString("otapw", "");
  vescMac  = prefs.getString("vescmac", "");
  if (kartId < 1 || kartId > GK_MAX_KARTS) kartId = DEFAULT_KART_ID;

  // OLED (Vext powers it, active low)
  pinMode(PIN_VEXT, OUTPUT); digitalWrite(PIN_VEXT, LOW); delay(50);
  oledOk = oled.begin();
  if (oledOk) { oled.clearBuffer(); oled.setFont(u8g2_font_6x10_tf); oled.drawStr(0, 12, "GoKart kart node"); oled.drawStr(0, 24, FW_VERSION); oled.sendBuffer(); }

  // LoRa front end, then radio
  pinMode(PIN_FEM_POWER, OUTPUT); digitalWrite(PIN_FEM_POWER, HIGH);
  pinMode(PIN_FEM_CSD, OUTPUT);   digitalWrite(PIN_FEM_CSD, HIGH);
  pinMode(PIN_FEM_CTX, OUTPUT);   digitalWrite(PIN_FEM_CTX, LOW);
  delay(5);
  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_NSS);
  radioStatus = radio.begin(GK_LORA_FREQ_MHZ, GK_LORA_BW_KHZ, GK_LORA_SF, GK_LORA_CR, GK_LORA_SYNC_WORD, LORA_TX_POWER_DBM, GK_LORA_PREAMBLE, LORA_TCXO_V, false);
  if (radioStatus == RADIOLIB_ERR_NONE) {
    radio.setDio2AsRfSwitch(true);
    radio.setRfSwitchPins(RADIOLIB_NC, PIN_FEM_CTX);
    radio.setCurrentLimit(140);
    radio.setPacketReceivedAction(onLoraIrq);
    radio.startReceive();
    radioOk = true;
  }

  // GNSS
  pinMode(PIN_GNSS_POWER, OUTPUT); digitalWrite(PIN_GNSS_POWER, LOW);
  pinMode(PIN_GNSS_WAKE, OUTPUT);  digitalWrite(PIN_GNSS_WAKE, HIGH);
  pinMode(PIN_GNSS_RST, OUTPUT);   digitalWrite(PIN_GNSS_RST, HIGH);
  pinMode(PIN_GNSS_PPS, INPUT);
  attachInterrupt(digitalPinToInterrupt(PIN_GNSS_PPS), onPps, RISING);
  gpsApplyCfg(0);

  // VESC
#if VESC_TRANSPORT_UART
  Serial2.begin(VESC_UART_BAUD, SERIAL_8N1, VESC_UART_RX_PIN, VESC_UART_TX_PIN);
  vesc.setPort(&Serial2);
#else
  char name[16]; snprintf(name, sizeof name, "gokart-%u", kartId);
  NimBLEDevice::init(name);
  NimBLEDevice::setMTU(512);
  NimBLEDevice::setPower(9);
#endif

  wifiStart();

  esp_task_wdt_config_t wdt = { .timeout_ms = WDT_TIMEOUT_S * 1000, .idle_core_mask = 0, .trigger_panic = true };
  if (esp_task_wdt_reconfigure(&wdt) != ESP_OK) esp_task_wdt_init(&wdt);
  esp_task_wdt_add(NULL);

  randomSeed(esp_random());
  memset(fleetMs, 0, sizeof fleetMs);
  logf("boot %s kart %u transport %s writes %s radio %s (%d)", FW_VERSION, kartId, VESC_TRANSPORT_UART ? "uart" : "ble", gkBool(writesEnabled()), radioOk ? "ok" : "FAILED", radioStatus);
}

void loop() {
  if (!otaInProgress) esp_task_wdt_reset();
  serviceUsb();
  serviceVesc();
  serviceWrites();
  serviceGps();
  serviceLoraRx();
  serviceLoraTx();
  serviceWifi();
  serviceOled();
  delay(1);
}
