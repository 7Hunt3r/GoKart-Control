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
volatile uint32_t loraIrqMicros = 0;
void IRAM_ATTR onLoraIrq() { loraIrq = true; loraIrqMicros = micros(); }
bool     radioOk = false;
int      radioStatus = -999;
uint32_t loraTx = 0, loraRx = 0, loraRxBad = 0;
int8_t   baseRssi = 0;
uint32_t baseHeardMs = 0;
uint8_t  txSeq = 0;
uint32_t lastTxMs = 0;
uint32_t lastTxEdge = 0, lastTxRound = 0xFFFFFFFF;
uint16_t txJitter = 0;
uint8_t  reportRate = 1;                      // 1 or 2 reports per second ("rate N" on the console)
// Beacon sync: when there is no GPS PPS, align slots to the pit beacon's second.
uint32_t syncEdgeMicros = 0, syncMs = 0;
uint32_t baseUtc = 0, baseUtcMs = 0; uint8_t pitKartsOnline = 0; uint8_t baseSyncSrc = 0;
uint8_t  racePos = 0, raceState = 0; uint32_t racePosMs = 0;   // from the pit beacon: running position (0 = none) and race state
// VESC identity pin (UUID from FW_VERSION), optional
String   vescUuid; bool vescIdentityBad = false; uint32_t vescIdCheckedMs = 0;

// ---------------------------------------------------------------- gps
TinyGPSPlus gps;
HardwareSerial& gpsSerial = Serial1;
struct GpsCfg { uint32_t baud; bool swap; };
// Pins are never swapped: the V4.3 wiring (RX 39 / TX 38) is verified, and driving the module's TX line from a
// swapped attempt left the UART dead until a power cycle (bench 2026-11-06). Only the baud is searched.
static const GpsCfg GPS_CFGS[] = { {9600,false}, {115200,false}, {38400,false} };
static const int GPS_CFG_N = sizeof(GPS_CFGS) / sizeof(GPS_CFGS[0]);
int      gpsCfgIdx = 0;
bool     gpsLocked = false, gpsConfigured = false; uint32_t gpsLockedMs = 0, gpsFixCount = 0, gpsLineMs = 0;
uint32_t gpsDetectStartMs = 0, gpsResetMs = 0, gpsConfigMs = 0; int gpsResets = 0;   // detection watchdog: hard-reset the module when no baud locks
bool     gpsLineOn = true;                 // {"t":"gps"} USB line on every new fix (up to 5 Hz) for the kart Pi
double   baseLat = 0, baseLon = 0; uint32_t baseFixMs = 0;   // pit radio's own fix from the beacon (for position-domain correction)
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
  size_t chunk = 20;   // negotiated MTU - 3; the VESC NRF51 UART bridge is usually MTU 23
  size_t write(const uint8_t* b, size_t n) override {
    if (!_tx) return 0;
    // The bridge is a transparent byte pipe; the VESC reassembles the packet, so chunk at the MTU.
    size_t sent = 0;
    while (sent < n) {
      size_t k = min(chunk, n - sent);
      bool ok = _tx->writeValue(b + sent, k, false);
      if (!ok) { ok = _tx->writeValue(b + sent, k, true); }
      if (!ok) return sent;
      sent += k;
    }
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
bool     slowActive = false; float slowErpm = 0; uint8_t slowMph = 0;
// Timed scale override for the game zones (boost pad / mud): auto-reverts, cleared by STOP/START, never above 1.0.
bool     ovActive = false; float ovScale = 1.0f; uint32_t ovUntilMs = 0;
// Race start: grid hold at zero power until GO. GO is issued_utc + arg when the kart has GPS/beacon time (all
// karts release together), else arg seconds after receipt. STOP keeps the kart stopped; START releases early.
bool     raceHold = false; uint32_t raceGoMs = 0; uint32_t raceGoUnix = 0;
// Limits as first read from this controller after link-up: everything we write echoes these
// (not the capture constants), so each kart keeps its own configuration.
VescActiveLimits origLimits; bool origValid = false;
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
  if (gps.date.isValid() && gps.time.isValid() && gps.date.age() <= 5000)
    return gkUnixFromCivil(gps.date.year(), gps.date.month(), gps.date.day(), gps.time.hour(), gps.time.minute(), gps.time.second());
  if (baseUtc && millis() - baseUtcMs < 120000) return baseUtc + (millis() - baseUtcMs) / 1000;   // pit beacon time
  return 0;
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
  t.slow_mph = slowActive ? slowMph : 0;
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
static float scaleTarget() {
  if (stopActive || raceHold) return 0.0f;
  if (ovActive) return ovScale;
  if (modeReq != GK_MODE_UNKNOWN) return MODES[modeReq].currentMaxScale;
  return origValid ? origLimits.currentMaxScale : 1.0f;
}
static float mphToErpm(float mph) {
  const VescActiveLimits& L = origLimits;
  if (!origValid || L.motorPoles == 0 || L.gearRatio <= 0 || L.wheelDiameter <= 0) return 0;
  float wheel_rpm = mph * 1609.34f / 60.0f / (3.14159265f * L.wheelDiameter);
  return wheel_rpm * L.gearRatio * (L.motorPoles / 2.0f);
}
static void requestBrake() {
  // "GKB" + amps + tenths of seconds -> the on-VESC brake script (no-op if none is loaded)
  uint8_t d[5] = { 'G', 'K', 'B', (uint8_t)STOP_BRAKE_AMPS, (uint8_t)(STOP_BRAKE_SECS * 10) };
  vesc.sendCustomAppData(d, sizeof d);
  logf("brake request sent to VESC script: %u A for %.1f s", STOP_BRAKE_AMPS, STOP_BRAKE_SECS);
}

static bool startWrite(float scale) {
  if (!writesEnabled() || !vescUp() || vescIdentityBad) return false;
  // Per-vehicle: every field except the motor-current scale is echoed back from the live
  // configuration just read out of THIS controller (GET_MCCONF). Never write before that read.
  if (!origValid) { logf("refusing write: this VESC's limits have not been read yet"); return false; }
  const VescActiveLimits& L = origLimits;
  VescTempLimits l;
  l.currentMinScale = L.currentMinScale;
  l.currentMaxScale = scale;
  l.minErpm = L.minErpm; l.maxErpm = L.maxErpm;
  if (slowActive) { l.maxErpm = slowErpm; if (l.minErpm < -slowErpm) l.minErpm = -slowErpm; }
  l.minDuty = L.minDuty; l.maxDuty = L.maxDuty;
  l.wattMin = L.wattMin; l.wattMax = L.wattMax;
  l.sendInCurrent = false;                     // battery limits: always the controller's own
  wAckSeen = vesc.lastAckMs();
  vesc.sendTempLimits(l);
  wState = W_SENT; wTarget = scale; wSentMs = millis();
  logf("vesc write scale %.3f", scale);
  return true;
}

static void serviceWrites() {
  uint32_t now = millis();
  if (raceHold && (int32_t)(now - raceGoMs) >= 0) {
    raceHold = false;
    Serial.printf("{\"t\":\"race\",\"kart\":%u,\"event\":\"go\"}\n", kartId);
    logf("race: GO");
    if (!stopActive && vescUp() && origValid) startWrite(scaleTarget());
  }
  if (ovActive && (int32_t)(now - ovUntilMs) >= 0) {
    ovActive = false; logf("boost over, back to %.2f", scaleTarget());
    if (!stopActive && vescUp() && origValid) startWrite(scaleTarget());
  }
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
        if (slowActive && fabsf(vesc.limits().maxErpm - slowErpm) > 50) ok = false;
        if (rampIdx < 0) cmdResult = ok ? GK_RES_APPLIED : GK_RES_UNKNOWN;
        modeConfirmed = ok && !stopActive && ((modeReq != GK_MODE_UNKNOWN && fabsf(wTarget - MODES[modeReq].currentMaxScale) < MODE_MATCH_TOL)
                                              || (ovActive && fabsf(wTarget - ovScale) < MODE_MATCH_TOL));
        logf("vesc write verify %s (scale %.3f)", ok ? "ok" : "MISMATCH", vesc.limits().currentMaxScale);
      } else if (now - wSentMs > VESC_ACK_TIMEOUT_MS) { wState = W_IDLE; if (rampIdx < 0) cmdResult = GK_RES_UNKNOWN; }
      break;
  }
}

// ---------------------------------------------------------------- commands
static void emitCmd(uint8_t src, uint8_t seq, uint8_t cmd, uint8_t arg) {
  Serial.printf("{\"t\":\"cmd\",\"kart\":%u,\"src\":%u,\"seq\":%u,\"cmd\":%u,\"arg\":%u,\"result\":%u}\n", kartId, src, seq, cmd, arg, cmdResult);
}

static uint8_t boostSecs = 0;   // set by the USB `boost` command just before handleCommand
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
      if (!stopActive) preStopScale = origValid ? origLimits.currentMaxScale : -1.0f;
      stopActive = true; modeConfirmed = false; ovActive = false; raceHold = false;
      requestBrake();                                   // regen via the VESC script, if present
      rampIdx = 0; rampNextMs = millis();
      break;
    case GK_CMD_START: {
      if (!writesEnabled()) { cmdResult = GK_RES_REJECTED; break; }
      stopActive = false; slowActive = false; ovActive = false; raceHold = false; rampIdx = -1;
      float s = scaleTarget();
      if (!origValid) { cmdResult = GK_RES_NO_VESC; break; }
      if (!startWrite(s)) cmdResult = GK_RES_NO_VESC;
      break;
    }
    case GK_CMD_RACE: {                                    // arg = seconds until GO; 0 or 100+position = race finished
      if (arg == 0 || (arg >= 100 && arg <= 100 + GK_MAX_KARTS)) {
        bool wasHold = raceHold; raceHold = false;
        Serial.printf("{\"t\":\"race\",\"kart\":%u,\"event\":\"finish\",\"position\":%d}\n", kartId, arg ? (int)(arg - 100) : 0);
        logf("race: finish%s", wasHold ? " (hold released)" : "");
        cmdResult = GK_RES_APPLIED;
        if (wasHold && !stopActive && vescUp() && origValid && writesEnabled()) { if (!startWrite(scaleTarget())) cmdResult = GK_RES_NO_VESC; }
        break;
      }
      if (arg > 30) { cmdResult = GK_RES_REJECTED; break; }
      uint32_t nowU = gpsUnix(); uint32_t goInMs;
      if (issued && nowU && issued + arg > nowU) goInMs = (issued + arg - nowU) * 1000UL;   // shared clock: same instant on every kart
      else if (issued && nowU) goInMs = 0;                                                 // arrived after GO: release now
      else goInMs = (uint32_t)arg * 1000UL;
      raceHold = true; raceGoMs = millis() + goInMs; raceGoUnix = issued ? issued + arg : 0; ovActive = false;
      Serial.printf("{\"t\":\"race\",\"kart\":%u,\"event\":\"countdown\",\"go_in_ms\":%lu,\"secs\":%u,\"go_utc\":%lu}\n", kartId, (unsigned long)goInMs, arg, (unsigned long)raceGoUnix);
      logf("race: grid hold, GO in %lu ms", (unsigned long)goInMs);
      if (!writesEnabled()) { cmdResult = GK_RES_REJECTED; break; }
      if (stopActive) break;                                 // already stopped: GO releases nothing until START
      if (!vescUp() || !origValid) { cmdResult = GK_RES_NO_VESC; break; }
      if (!startWrite(0.0f)) cmdResult = GK_RES_NO_VESC;
      break;
    }
    case GK_CMD_BOOST: {                                   // arg = scale percent; duration from boostSecs (USB) or 5 s
      if (!writesEnabled()) { cmdResult = GK_RES_REJECTED; break; }
      if (arg < 10 || arg > 100 || stopActive) { cmdResult = GK_RES_REJECTED; break; }
      if (!vescUp() || !origValid) { cmdResult = GK_RES_NO_VESC; break; }
      ovScale = arg / 100.0f; ovActive = true; ovUntilMs = millis() + (boostSecs ? boostSecs : 5) * 1000UL;
      logf("boost: scale %.2f for %u s", ovScale, boostSecs ? boostSecs : 5); boostSecs = 0;
      if (!startWrite(scaleTarget())) cmdResult = GK_RES_NO_VESC;
      break;
    }
    case GK_CMD_SLOW: {
      if (!writesEnabled()) { cmdResult = GK_RES_REJECTED; break; }
      if (!vescUp() || !origValid) { cmdResult = GK_RES_NO_VESC; break; }
      float mph = arg ? (float)arg : (float)SLOW_DEFAULT_MPH;
      float e = mphToErpm(mph);
      if (e < 100) { cmdResult = GK_RES_REJECTED; logf("slow: drivetrain numbers unavailable"); break; }
      slowActive = true; slowErpm = e; slowMph = (uint8_t)(mph > 255 ? 255 : mph);
      logf("slow: cap %.0f mph = %.0f ERPM", mph, e);
      if (!startWrite(scaleTarget())) cmdResult = GK_RES_NO_VESC;
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
  { uint16_t mtu = pClient->getMTU(); bleStream.chunk = (mtu > 23) ? (size_t)(mtu - 3) : 20; }
  vesc.setPort(&bleStream);
  bleConnected = true; bleConnectedMs = millis();
  logf("ble connected %s mtu %u (write chunk %u)", target.toString().c_str(), (unsigned)pClient->getMTU(), (unsigned)bleStream.chunk);
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
    modeConfirmed = false; vescIdentityBad = false; vescIdCheckedMs = 0; origValid = false;
    logf("vesc link up");
  }
  if (up && !origValid && vesc.limits().receivedMs && wState == W_IDLE) {
    origLimits = vesc.limits(); origValid = true;
    logf("vesc limits read: Imax %.0f A, ERPM %.0f..%.0f, duty %.3f..%.3f, scale %.2f/%.2f, poles %u gear %.3f wheel %.3f m",
         origLimits.currentMax, origLimits.minErpm, origLimits.maxErpm, origLimits.minDuty, origLimits.maxDuty,
         origLimits.currentMinScale, origLimits.currentMaxScale, origLimits.motorPoles, origLimits.gearRatio, origLimits.wheelDiameter);
  }
  if (up && vesc.fwSeenMs && vesc.fwSeenMs != vescIdCheckedMs) {
    vescIdCheckedMs = vesc.fwSeenMs;
    bool bad = vescUuid.length() == 24 && !vescUuid.equalsIgnoreCase(vesc.uuidHex);
    if (bad != vescIdentityBad) { vescIdentityBad = bad; logf(bad ? "VESC IDENTITY MISMATCH: uuid %s, pinned %s; writes refused" : "vesc identity ok uuid %s", vesc.uuidHex, vescUuid.c_str()); }
    else if (!bad) logf("vesc fw %u.%02u %s uuid %s", vesc.fwMajor, vesc.fwMinor, vesc.hwName, vesc.uuidHex);
  }
  if (!up && vescLinkWas) { modeConfirmed = false; logf("vesc link lost"); }
  vescLinkWas = up;
  if (now - vescPollMs >= VESC_POLL_MS) { vescPollMs = now; vesc.requestValues(); }
  if (up && now - vescMcconfMs >= VESC_MCCONF_REFRESH_MS && wState == W_IDLE) { vescMcconfMs = now; vesc.requestMotorConfig(); }
}

// ---------------------------------------------------------------- gps
static bool gpsSerialUp = false;
static void gpsApplyCfg(int idx) {
  const GpsCfg& c = GPS_CFGS[idx];
  if (!gpsSerialUp) { gpsSerial.setRxBufferSize(2048); gpsSerial.begin(c.baud, SERIAL_8N1, PIN_GNSS_RX, PIN_GNSS_TX); gpsSerialUp = true; }
  else gpsSerial.updateBaudRate(c.baud);              // never end()/begin() again: keeps the pin matrix and driver state intact
  while (gpsSerial.available()) gpsSerial.read();
  gpsCfgIdx = idx; gpsCfgStartMs = millis(); gpsGoodAtStart = gps.passedChecksum();
}

// Quectel L76K (CASIC): send a $PCAS sentence with its checksum.
static void gpsSend(const char* body) {
  uint8_t cs = 0; for (const char* p = body; *p; p++) cs ^= (uint8_t)*p;
  gpsSerial.printf("$%s*%02X\r\n", body, cs); delay(30);
}
// Once the module answers: all three constellations it can track, GGA+RMC only, 5 Hz, 115200 baud.
// Config is re-sent after every (re)detection, so a module that lost power comes back the same way.
// Blind recovery for a module whose NMEA output got switched off (seen 2026-11-06: output disabled, PPS still
// ticking, and the setting survived a VCC cycle because the module has backup power). Re-enable the sentences and
// 1 Hz at every baud we know, and put the module at 9600. Then the normal search finds it.
static void gpsRecover(const char* why) {
  logf("gps recover (%s)", why);
  const uint32_t bauds[] = {115200, 38400, 9600};
  for (uint32_t b : bauds) {
    gpsSerial.updateBaudRate(b); delay(20);
    gpsSend("PCAS03,1,1,1,1,1,1,0,0,0,0,,,0,0");      // GGA GLL GSA GSV RMC VTG on
    gpsSend("PCAS02,1000");                           // 1 Hz
    if (b != 9600) gpsSend("PCAS01,1");               // and come to 9600
    delay(150);
  }
  gpsLocked = false; gpsConfigured = false; gpsDetectStartMs = millis();
  gpsApplyCfg(0);
}

static void gpsConfigure() {
  gpsSend("PCAS03,1,0,0,0,1,0,0,0,0,0,,,0,0");  // GGA and RMC only (no GSV/GSA/VTG/GLL chatter)
  gpsSend("PCAS02,200");                        // 5 Hz position updates: two sentences at 5 Hz is ~725 B/s, fits 9600
  gpsSend("PCAS04,7");                          // GPS + BDS + GLONASS. LAST: the module restarts on a constellation
  delay(600);                                   // change and drops whatever arrives during the restart.
  // The baud is deliberately left alone: at 9600 every reboot finds the module on the first try.
  gpsConfigured = true; gpsConfigMs = millis(); logf("gps configured: GGA+RMC, 5 Hz, GPS+BDS+GLONASS at %lu", (unsigned long)GPS_CFGS[gpsCfgIdx].baud);
}

// Power-cycle + reset the L76K (it comes back at its 9600/1 Hz defaults and gets configured again).
static void gpsHardReset(const char* why) {
  gpsResets++; gpsResetMs = millis();
  logf("gps hard reset (%s), #%d", why, gpsResets);
  digitalWrite(PIN_GNSS_POWER, HIGH); delay(300);          // P-FET: HIGH = off
  digitalWrite(PIN_GNSS_RST, LOW);
  digitalWrite(PIN_GNSS_POWER, LOW);  delay(150);
  digitalWrite(PIN_GNSS_RST, HIGH);   delay(100);
  gpsLocked = false; gpsConfigured = false; gpsDetectStartMs = millis();
  gpsRecover("after hard reset");
}

static void serviceGps() {
  while (gpsSerial.available()) { gps.encode(gpsSerial.read()); gpsLastCharMs = millis(); }
  uint32_t now = millis();
  if (!gpsLocked) {
    if (gps.passedChecksum() > gpsGoodAtStart + 2) {
      gpsLocked = true; gpsLockedMs = now; gpsConfigured = false; prefs.putUChar("gpscfg", (uint8_t)gpsCfgIdx);
      logf("gps found: %lu baud%s", (unsigned long)GPS_CFGS[gpsCfgIdx].baud, GPS_CFGS[gpsCfgIdx].swap ? " (pins swapped)" : "");
    }
    else if (now - gpsCfgStartMs > GPS_DETECT_STEP_MS) gpsApplyCfg((gpsCfgIdx + 1) % GPS_CFG_N);
    // nothing parses at any baud for two full sweeps: the module is wedged or at an odd setting -> reset it
    if (!gpsLocked && now - gpsDetectStartMs > 2UL * GPS_CFG_N * GPS_DETECT_STEP_MS && now - gpsResetMs > 120000UL && gpsResets < 20) gpsHardReset("no baud locked");
  } else if (now - gpsLastCharMs > (gpsConfigured && now - gpsConfigMs < 10000 ? 3000UL : 15000UL)) {
    gpsLocked = false; gpsDetectStartMs = now; logf("gps silent, re-detecting");
    if (gpsConfigured && now - gpsConfigMs < 10000) gpsRecover("silent after configure"); else gpsApplyCfg(gpsCfgIdx);
  }
  else if (!gpsConfigured && now - gpsLockedMs > 500) gpsConfigure();
  if (gps.location.isValid() && gps.location.isUpdated()) {
    fixLat = gps.location.lat(); fixLon = gps.location.lng(); lastFixMs = now; gpsFixCount++;
    if (gpsLineOn) {           // fast fix line for the kart Pi: position, Doppler speed/course, quality
      Serial.printf("{\"t\":\"gps\",\"kart\":%u,\"lat\":%.7f,\"lon\":%.7f,\"kph\":%.1f,\"heading\":%.1f,\"hdop\":%.2f,\"sats\":%u,\"ms\":%lu}\n",
                    kartId, fixLat, fixLon, gps.speed.isValid() ? gps.speed.kmph() : 0.0, gps.course.isValid() ? gps.course.deg() : 0.0,
                    gps.hdop.isValid() ? gps.hdop.hdop() : 99.0, gps.satellites.isValid() ? (unsigned)gps.satellites.value() : 0, (unsigned long)now);
    }
  }
}

// ---------------------------------------------------------------- lora
static void emitFleet(uint8_t id, float rssi, float snr) {
  char buf[640];
  gkTelemetryToJson(buf, sizeof buf, fleet[id], "fleet", rssi, snr, millis());
  Serial.println(buf);
}

static void serviceLoraRxOne();
static void serviceLoraRx() {
  for (int i = 0; i < 4 && radioOk && loraIrq; i++) serviceLoraRxOne();   // drain back-to-back packets
}
static void serviceLoraRxOne() {
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
      if (h->src >= 1 && h->src <= GK_MAX_KARTS && h->src != kartId) { gkCopyTelemetry(fleet[h->src], buf, len); fleetMs[h->src] = millis(); emitFleet(h->src, rssi, snr); }
      break;
    case GK_MSG_COMMAND: {
      GkCommandMsg c; memcpy(&c, buf, len);
      if (gkAddressedToMe(c.h, kartId)) handleCommand(c.h.src, c.h.seq, c.cmd, c.arg, c.ttl_s, c.issued_utc_s);
      break;
    }
    case GK_MSG_BASE_INFO: {
      GkBaseInfo b; gkCopyBeacon(b, buf, len);
      static uint32_t directBeaconMs = 0;
      bool relayed = gkIsRelayed(b.h);
      if (!relayed) directBeaconMs = millis();
      if (!relayed || !directBeaconMs || millis() - directBeaconMs > 1500) {   // sync from the repeater only when the pit itself is out of reach
        uint32_t extra = relayed ? (GK_RELAY_DELAY_MS + GK_BEACON_AIRTIME_MS) : 0;   // one extra hop
        syncEdgeMicros = loraIrqMicros - ((uint32_t)b.phase_ms + GK_BEACON_AIRTIME_MS + extra) * 1000UL;
        syncMs = millis();
      }
      baseSyncSrc = b.sync_src; pitKartsOnline = b.karts_online;
      if (len >= (size_t)sizeof(GkBaseInfo)) { uint8_t p = gkBeaconPos(b, kartId); if (p != racePos || b.race_state != raceState) Serial.printf("{\"t\":\"race\",\"kart\":%u,\"event\":\"position\",\"position\":%u,\"state\":%u}\n", kartId, p, b.race_state); racePos = p; raceState = b.race_state; racePosMs = millis(); }
      if (b.lat_e7 && b.lon_e7) { baseLat = b.lat_e7 / 1e7; baseLon = b.lon_e7 / 1e7; baseFixMs = millis(); }
      if (b.utc_s) { baseUtc = b.utc_s; baseUtcMs = millis(); }
      break;
    }
    default: break;
  }
}

static bool syncFresh() { return syncMs && millis() - syncMs < GK_SYNC_MAX_AGE_MS; }
static bool slotDue() {
  uint32_t now = millis();
  uint32_t edge = 0; bool have = false;
  if (ppsFresh()) { noInterrupts(); edge = ppsMicros; interrupts(); have = true; }
  else if (syncFresh()) { edge = syncEdgeMicros; have = true; }
  if (have) {
    uint32_t periodUs = 1000000UL / reportRate;
    uint32_t slotUs = (reportRate == 2 ? GK_SLOT_MS_2HZ : GK_SLOT_MS) * 1000UL;
    uint32_t since = micros() - edge;             // the edge may be a few seconds old (beacon sync)
    uint32_t round = since / periodUs, inRound = since % periodUs;
    int32_t start = (int32_t)((kartId - 1) * slotUs) - GK_SLOT_GUARD_MS * 1000;
    if (edge == lastTxEdge && round == lastTxRound) return false;
    if ((int32_t)inRound >= start && (int32_t)inRound < start + 40000) { lastTxEdge = edge; lastTxRound = round; return true; }
    return false;
  }
  return (now - lastTxMs) >= (uint32_t)(1000 / reportRate + txJitter);
}

static void serviceLoraTx() {
  if (!radioOk || !slotDue()) return;
  GkTelemetry t; fillTelemetry(t);
  digitalWrite(PIN_LED, HIGH);
  int st = radio.transmit((uint8_t*)&t, sizeof t);
  loraIrq = false;                       // the TX-done interrupt also raised DIO1
  radio.startReceive();
  digitalWrite(PIN_LED, LOW);
  lastTxMs = millis(); txJitter = random(0, reportRate == 2 ? 80 : 150); txSeq++;
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
  char buf[720];
  int n = gkTelemetryToJson(buf, sizeof buf, t, "self", 0, 0, millis());
  if (n > 0 && n < (int)sizeof buf - 160 && buf[n - 1] == '}') {       // USB-only extras: fix quality and the pit radio's own fix
    uint32_t now = millis();
    char bl[24] = "null", bo[24] = "null", ba[12] = "null";
    if (baseFixMs && now - baseFixMs < 30000) { snprintf(bl, sizeof bl, "%.7f", baseLat); snprintf(bo, sizeof bo, "%.7f", baseLon); snprintf(ba, sizeof ba, "%lu", (unsigned long)((now - baseFixMs) / 1000)); }
    bool rp = racePosMs && now - racePosMs < 30000;
    snprintf(buf + n - 1, sizeof buf - n + 1, ",\"hdop\":%.2f,\"fix_age_ms\":%lu,\"gps_hz\":%u,\"base_lat\":%s,\"base_lon\":%s,\"base_fix_age_s\":%s,\"race_pos\":%u,\"race_state\":%u}",
             gps.hdop.isValid() ? gps.hdop.hdop() : 99.0, lastFixMs ? (unsigned long)(now - lastFixMs) : 999999UL, gpsConfigured ? 5 : 1, bl, bo, ba, rp ? racePos : 0, rp ? raceState : 0);
  }
  Serial.println(buf);
}

static void usbStatus() {
  const VescActiveLimits& L = vesc.limits();
  char hdopStr[12] = "null"; if (gps.hdop.isValid()) snprintf(hdopStr, sizeof hdopStr, "%.2f", gps.hdop.hdop());
  Serial.printf("{\"t\":\"status\",\"kart\":%u,\"fw\":\"%s\",\"built\":\"" __DATE__ " " __TIME__ "\",\"writes\":%s,\"transport\":\"%s\",\"radio\":%s,\"radio_status\":%d,"
                "\"lora_tx\":%lu,\"lora_rx\":%lu,\"lora_rx_bad\":%lu,\"gps_locked\":%s,\"gps_cfg\":\"%lu%s\",\"pps_count\":%lu,"
                "\"vesc_fw\":\"%u.%02u\",\"vesc_hw\":\"%s\",\"vesc_packets\":%lu,\"vesc_crc_err\":%lu,\"vesc_limits\":%s,"
                "\"l_current_max\":%.1f,\"l_in_current_max\":%.1f,\"scale_max\":%.3f,\"scale_min\":%.3f,\"max_erpm\":%.0f,\"max_duty\":%.3f,"
                "\"poles\":%u,\"gear\":%.4f,\"wheel_m\":%.4f,\"wifi\":\"%s\",\"ssid\":\"%s\",\"vescmac\":\"%s\","
                "\"vesc_uuid\":\"%s\",\"uuid_pin\":\"%s\",\"id_ok\":%s,\"rate\":%u,\"sync\":\"%s\",\"pit_karts_online\":%u,"
                "\"gps_fix\":%s,\"gps_sats\":%u,\"gps_hdop\":%s,\"gps_age_ms\":%lu,\"gps_lat\":%.6f,\"gps_lon\":%.6f,\"heap\":%lu}\n",
                kartId, FW_VERSION, gkBool(writesEnabled()), VESC_TRANSPORT_UART ? "uart" : "ble", gkBool(radioOk), radioStatus,
                (unsigned long)loraTx, (unsigned long)loraRx, (unsigned long)loraRxBad, gkBool(gpsLocked),
                (unsigned long)GPS_CFGS[gpsCfgIdx].baud, GPS_CFGS[gpsCfgIdx].swap ? "s" : "", (unsigned long)ppsCount,
                vesc.fwMajor, vesc.fwMinor, vesc.hwName, (unsigned long)vesc.packets, (unsigned long)vesc.crcErrors, gkBool(L.receivedMs != 0),
                L.currentMax, L.inCurrentMax, L.currentMaxScale, L.currentMinScale, L.maxErpm, L.maxDuty,
                L.motorPoles, L.gearRatio, L.wheelDiameter,
                WiFi.status() == WL_CONNECTED ? WiFi.localIP().toString().c_str() : "", wifiSsid.c_str(), vescMac.c_str(),
                vesc.uuidHex, vescUuid.c_str(), gkBool(!vescIdentityBad), reportRate, ppsFresh() ? "pps" : (syncFresh() ? "beacon" : "free"), pitKartsOnline,
                gkBool(gps.location.isValid() && gps.location.age() < 3000), gps.satellites.isValid() ? (unsigned)gps.satellites.value() : 0,
                hdopStr, (unsigned long)gps.location.age(), gps.location.isValid() ? gps.location.lat() : 0.0, gps.location.isValid() ? gps.location.lng() : 0.0,
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
  else if (!strcmp(line, "slow"))  handleCommand(0xFE, ++usbSeq, GK_CMD_SLOW, arg ? atoi(arg) : 0, 0, 0);
  else if (!strcmp(line, "race"))  handleCommand(0xFE, ++usbSeq, GK_CMD_RACE, arg ? atoi(arg) : 5, 0, 0);
  else if (!strcmp(line, "boost")) {                      // boost SECS SCALE   e.g. "boost 5 1.0" or "boost 4 0.5" (mud)
    int secs = 5; float sc = 1.0f;
    if (arg) { secs = atoi(arg); char* a2 = strchr(arg, ' '); if (a2) sc = atof(a2 + 1); }
    if (secs < 1 || secs > 30 || sc < 0.1f || sc > 1.0f) logf("usage: boost SECS(1-30) SCALE(0.10-1.00)");
    else { boostSecs = (uint8_t)secs; handleCommand(0xFE, ++usbSeq, GK_CMD_BOOST, (uint8_t)lroundf(sc * 100), 0, 0); }
  }
  else if (!strcmp(line, "wifi") && arg) {
    char* pw = strchr(arg, ' '); if (pw) { *pw++ = 0; }
    prefs.putString("ssid", arg); prefs.putString("pass", pw ? pw : "");
    logf("wifi credentials stored for '%s'; reboot to apply", arg);
  }
  else if (!strcmp(line, "otapw") && arg) { prefs.putString("otapw", arg); logf("ota password stored; reboot to apply"); }
  else if (!strcmp(line, "rate") && arg) { int r = atoi(arg); if (r == 1 || r == 2) { reportRate = r; prefs.putUChar("rate", r); logf("report rate %d Hz", r); } else logf("rate must be 1 or 2"); }
  else if (!strcmp(line, "vescuuid") && arg) {
    if (!strcmp(arg, "clear")) { prefs.remove("vescuuid"); vescUuid = ""; vescIdentityBad = false; logf("vesc uuid pin cleared"); }
    else if (!strcmp(arg, "now") && vesc.uuidHex[0]) { vescUuid = vesc.uuidHex; prefs.putString("vescuuid", vescUuid); vescIdentityBad = false; logf("vesc uuid pinned to %s", vescUuid.c_str()); }
    else if (strlen(arg) == 24) { vescUuid = arg; prefs.putString("vescuuid", vescUuid); vescIdCheckedMs = 0; logf("vesc uuid pinned to %s", arg); }
    else logf("usage: vescuuid <24 hex> | now | clear");
  }
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
  else if (!strcmp(line, "gpsreset")) gpsHardReset("console");
  else if (!strcmp(line, "gpsrecover")) gpsRecover("console");
  else if (!strcmp(line, "gpscold")) {                    // 5 s power-off (drain backup), WAKE pulse, then watch the TX line at 9600 for 4 s
    digitalWrite(PIN_GNSS_POWER, HIGH); digitalWrite(PIN_GNSS_WAKE, LOW); delay(5000);
    digitalWrite(PIN_GNSS_RST, LOW); digitalWrite(PIN_GNSS_POWER, LOW); delay(200); digitalWrite(PIN_GNSS_RST, HIGH); delay(50); digitalWrite(PIN_GNSS_WAKE, HIGH);
    gpsSerial.updateBaudRate(9600); while (gpsSerial.available()) gpsSerial.read();
    uint32_t t0 = millis(), edges = 0, bytes = 0; int last = digitalRead(PIN_GNSS_RX); char first[61] = ""; size_t n = 0;
    while (millis() - t0 < 4000) { int a = digitalRead(PIN_GNSS_RX); if (a != last) { edges++; last = a; } while (gpsSerial.available()) { int c = gpsSerial.read(); bytes++; if (n < 60 && c >= 32 && c < 127 && c != '"' && c != '\\') first[n++] = (char)c; } }
    first[n] = 0;
    logf("gpscold: %lu rx edges, %lu bytes in 4 s after a 5 s power-off: %s", (unsigned long)edges, (unsigned long)bytes, first);
    gpsLocked = false; gpsConfigured = false; gpsDetectStartMs = millis(); gpsApplyCfg(0);
  }
  else if (!strcmp(line, "gpspin")) {                     // hardware check: edges on the RX pad (39) and PPS (41) in 1 s
    uint32_t t0 = millis(), e39 = 0, e41 = 0; int l39 = digitalRead(PIN_GNSS_RX), l41 = digitalRead(PIN_GNSS_PPS), h39 = 0, n = 0;
    while (millis() - t0 < 1000) { int a = digitalRead(PIN_GNSS_RX), b = digitalRead(PIN_GNSS_PPS); if (a != l39) { e39++; l39 = a; } if (b != l41) { e41++; l41 = b; } h39 += a; n++; }
    logf("gpspin: rx edges %lu (high %d%%), pps edges %lu, pwr pin %d, wake %d, rst %d, pps_count %lu", (unsigned long)e39, n ? 100 * h39 / n : 0, (unsigned long)e41, digitalRead(PIN_GNSS_POWER), digitalRead(PIN_GNSS_WAKE), digitalRead(PIN_GNSS_RST), (unsigned long)ppsCount);
  }
  else if (!strcmp(line, "gpsdump")) {                    // raw UART bytes for 2 s, escaped, so a silent/garbled module is obvious
    char dump[321]; size_t n = 0; uint32_t t0 = millis(); uint32_t total = 0;
    while (millis() - t0 < 2000) { while (gpsSerial.available()) { int c = gpsSerial.read(); total++; if (n < 300) { if (c >= 32 && c < 127 && c != '"' && c != '\\') dump[n++] = (char)c; else n += snprintf(dump + n, sizeof dump - n, "<%02X>", c & 0xFF); } } delay(1); }
    dump[n < 320 ? n : 320] = 0;
    logf("gpsdump %lu bytes at %lu baud locked=%d: %s", (unsigned long)total, (unsigned long)GPS_CFGS[gpsCfgIdx].baud, gpsLocked, dump);
  }
  else if (!strcmp(line, "gpstest") && arg) {             // gpstest 9600|115200|38400: listen 3 s at that baud and count good sentences
    uint32_t b = (uint32_t)atol(arg); int idx = -1;
    for (int i = 0; i < GPS_CFG_N; i++) if (GPS_CFGS[i].baud == b) idx = i;
    if (idx < 0) logf("gpstest: baud must be one of 9600/115200/38400");
    else {
      gpsLocked = false; gpsApplyCfg(idx); uint32_t g0 = gps.passedChecksum(), f0 = gps.failedChecksum(), t0 = millis(), total = 0;
      while (millis() - t0 < 3000) { while (gpsSerial.available()) { gps.encode(gpsSerial.read()); total++; } delay(1); }
      logf("gpstest %lu: %lu bytes, %lu good, %lu bad sentences in 3 s", (unsigned long)b, (unsigned long)total, (unsigned long)(gps.passedChecksum() - g0), (unsigned long)(gps.failedChecksum() - f0));
      gpsDetectStartMs = millis();
    }
  }
  else if (!strcmp(line, "gpsline") && arg) { gpsLineOn = !strcmp(arg, "on"); prefs.putBool("gpsline", gpsLineOn); logf("gps fix line %s", gpsLineOn ? "on" : "off"); }
  else if (!strcmp(line, "reboot")) { logf("rebooting"); delay(100); ESP.restart(); }
  else if (!strcmp(line, "help")) logf("commands: status | id N | mode 0..2 | stop | start | slow [mph] | boost SECS SCALE | race [secs] | gpsreset | gpsrecover | gpsdump | gpstest BAUD | ping | wifi SSID PASS | otapw PASS | vescmac XX:XX:XX:XX:XX:XX | vescmac clear | blescan | vescuuid <hex>|now|clear | rate 1|2 | reboot");
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
  else if (raceHold) { oled.drawBox(94, 0, 34, 11); oled.setDrawColor(0); oled.drawStr(98, 9, "GRID"); oled.setDrawColor(1); }
  else if (racePos && racePosMs && millis() - racePosMs < 30000) { char pb[6]; snprintf(pb, sizeof pb, "P%u", racePos); oled.drawBox(94, 0, 34, 11); oled.setDrawColor(0); oled.drawStr(104, 9, pb); oled.setDrawColor(1); }
  else if (ovActive) { oled.drawBox(94, 0, 34, 11); oled.setDrawColor(0); oled.drawStr(96, 9, ovScale >= (modeReq != GK_MODE_UNKNOWN ? MODES[modeReq].currentMaxScale : 1.0f) ? "BOOST" : "MUD"); oled.setDrawColor(1); }
  else if (slowActive) { oled.drawBox(94, 0, 34, 11); oled.setDrawColor(0); oled.drawStr(98, 9, "SLOW"); oled.setDrawColor(1); }
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
  if (vescIdentityBad) snprintf(l, sizeof l, "VESC ID MISMATCH!");
  else if (baseHeardMs) snprintf(l, sizeof l, "pit %ddBm %lus %s", baseRssi, (unsigned long)((millis() - baseHeardMs) / 1000), ppsFresh() ? "PPS" : (syncFresh() ? "sync" : ""));
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
  vescUuid = prefs.getString("vescuuid", "");
  reportRate = prefs.getUChar("rate", 2); if (reportRate != 1) reportRate = 2;   // default 2 Hz (owner: "go faster")
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
  gpsLineOn = prefs.getBool("gpsline", true);
  gpsDetectStartMs = millis();
  { uint8_t c = prefs.getUChar("gpscfg", 0); gpsApplyCfg(c < GPS_CFG_N ? c : 0); }   // try the last baud that worked first

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
  logf("boot %s (built " __DATE__ " " __TIME__ ") kart %u transport %s writes %s radio %s (%d)", FW_VERSION, kartId, VESC_TRANSPORT_UART ? "uart" : "ble", gkBool(writesEnabled()), radioOk ? "ok" : "FAILED", radioStatus);
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
  serviceLoraRx();                       // again after the slow OLED/WiFi work
  delay(1);
}
