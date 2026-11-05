// GoKartProtocol.h — shared LoRa packet format for GoKart Control.
//
// Used by firmware/kart-heltec-v4 (kart side) and firmware/base-tbeam-s3 (pit
// side). Everything on the air is one of the packed structs below,
// little-endian, with GkHeader first. Keep packets small: airtime is the scarce
// resource with eight karts sharing one channel.
//
// Status: DESIGNED AND COMPILED, NOT YET EXERCISED OVER THE AIR.
#pragma once
#include <stdint.h>
#include <string.h>

// ---- Protocol identity -----------------------------------------------------
#define GK_PROTO_VERSION   1
#define GK_FLEET_ID        0x4B   // packets with another fleet id are ignored
#define GK_MAX_KARTS       8
#define GK_ADDR_BASE       0      // pit base station
#define GK_ADDR_ALL        0xFF   // broadcast

// ---- Radio parameters (identical on both ends) ----------------------------
// US ISM band. SF7/BW250 keeps a 46-byte telemetry packet near 40 ms airtime,
// which is what makes 8 karts at 1 Hz fit in 8 x 100 ms slots. Change SF/BW on
// both ends together. Range is traded for airtime here; raise SF if the track
// proves too big and lower the report rate to match.
#define GK_LORA_FREQ_MHZ       915.0
#define GK_LORA_BW_KHZ         250.0
#define GK_LORA_SF             7
#define GK_LORA_CR             5       // 4/5
#define GK_LORA_SYNC_WORD      0x12    // RadioLib "private" sync word, same on SX127x/SX126x
#define GK_LORA_PREAMBLE       8

// ---- Slot timing -----------------------------------------------------------
// Karts with a GPS PPS edge align their transmit to the top of the UTC second:
// kart k transmits at (k-1) * GK_SLOT_MS after the edge. Without PPS a kart
// free-runs at 1 Hz with random jitter and simply risks collisions. The base
// station sends commands in the tail of the second (after slot 8) when it has
// PPS, otherwise immediately.
#define GK_REPORT_PERIOD_MS    1000
#define GK_SLOT_MS             100
#define GK_SLOT_GUARD_MS       8      // early offset so the preamble starts inside the slot
#define GK_BASE_WINDOW_MS      (GK_MAX_KARTS * GK_SLOT_MS + 20)

// ---- Message types ---------------------------------------------------------
enum GkMsgType : uint8_t {
  GK_MSG_TELEMETRY = 0x01,   // kart -> everyone, once per second
  GK_MSG_COMMAND   = 0x02,   // base -> one kart or all
  GK_MSG_BASE_INFO = 0x03,   // base -> everyone (base position, time), optional
};

// ---- Commands --------------------------------------------------------------
enum GkCommand : uint8_t {
  GK_CMD_NONE     = 0,
  GK_CMD_SET_MODE = 1,   // arg = mode index (0 ECO, 1 NORMAL, 2 SPORT)
  GK_CMD_STOP     = 2,   // ramp motor current scale to zero and hold
  GK_CMD_START    = 3,   // release stop, re-apply requested mode
  GK_CMD_PING     = 4,   // no-op; kart reports the seq back
};

// Result of the last command a kart received, reported inside telemetry.
enum GkCmdResult : uint8_t {
  GK_RES_NONE      = 0,
  GK_RES_ACCEPTED  = 1,   // received, being applied (e.g. stop ramp in progress)
  GK_RES_APPLIED   = 2,   // VESC acknowledged and re-read confirms
  GK_RES_NO_VESC   = 3,   // received but the VESC link is down
  GK_RES_REJECTED  = 4,   // bad argument, expired, or writes disabled
  GK_RES_UNKNOWN   = 5,   // sent to VESC, no ack within timeout
};

// ---- Modes -----------------------------------------------------------------
enum GkMode : uint8_t { GK_MODE_ECO = 0, GK_MODE_NORMAL = 1, GK_MODE_SPORT = 2, GK_MODE_COUNT = 3, GK_MODE_UNKNOWN = 255 };
static inline const char* gkModeName(uint8_t m) {
  switch (m) { case GK_MODE_ECO: return "ECO"; case GK_MODE_NORMAL: return "NORMAL"; case GK_MODE_SPORT: return "SPORT"; }
  return "?";
}

// ---- Telemetry status flags ------------------------------------------------
#define GK_FLAG_GPS_FIX        (1 << 0)   // lat/lon valid and < 3 s old
#define GK_FLAG_PPS            (1 << 1)   // slot timing is PPS-aligned
#define GK_FLAG_VESC_LINK      (1 << 2)   // GET_VALUES answered within the last second
#define GK_FLAG_STOPPED        (1 << 3)   // stop is active (current scale held at 0)
#define GK_FLAG_MODE_CONFIRMED (1 << 4)   // VESC re-read matches the requested mode
#define GK_FLAG_FAULT          (1 << 5)   // VESC fault code non-zero
#define GK_FLAG_WIFI           (1 << 6)   // WiFi up (OTA reachable)
#define GK_FLAG_WRITES_ENABLED (1 << 7)   // this firmware is built with VESC writes enabled

// ---- Wire structs ----------------------------------------------------------
#pragma pack(push, 1)
struct GkHeader {
  uint8_t version;   // GK_PROTO_VERSION
  uint8_t fleet;     // GK_FLEET_ID
  uint8_t type;      // GkMsgType
  uint8_t src;       // kart id 1..8, or GK_ADDR_BASE
  uint8_t dst;       // kart id, GK_ADDR_BASE or GK_ADDR_ALL
  uint8_t seq;       // per-sender rolling counter; for commands this is the command id
};

struct GkTelemetry {
  GkHeader h;
  int32_t  lat_e7;        // degrees * 1e7 (0 when no fix; check GK_FLAG_GPS_FIX)
  int32_t  lon_e7;
  uint16_t gps_kph_x10;   // km/h * 10 from GPS ground speed
  uint16_t heading_x10;   // degrees * 10
  uint8_t  sats;
  uint8_t  flags;         // GK_FLAG_*
  uint16_t volts_x10;     // VESC input voltage * 10
  int16_t  motor_a_x10;   // average motor current * 10
  int16_t  batt_a_x10;    // average battery current * 10
  int32_t  erpm;          // signed electrical rpm
  int8_t   duty_pct;      // duty cycle * 100
  int16_t  esc_temp_x10;  // MOSFET temperature * 10
  uint8_t  fault;         // mc_fault_code
  uint8_t  mode_req;      // GkMode last requested on this kart (255 = none since boot)
  uint8_t  mode_actual;   // GkMode reconciled from the VESC's live current scale (255 = unknown)
  uint8_t  last_cmd_seq;  // seq of the last command this kart processed
  uint8_t  cmd_result;    // GkCmdResult for that command
  uint16_t ah_x100;       // amp-hours drawn since VESC boot * 100
  uint16_t uptime_s;      // kart controller uptime (wraps at 18 h)
  int8_t   base_rssi;     // RSSI of the last base packet heard, dBm (0 = none)
  uint8_t  gps_age_s;     // seconds since last valid fix (255 = never)
  uint16_t scale_x1000;   // live l_current_max_scale read back from the VESC * 1000 (65535 = unknown)
};

struct GkCommandMsg {
  GkHeader h;             // h.dst = kart id or GK_ADDR_ALL, h.seq = command id
  uint8_t  cmd;           // GkCommand
  uint8_t  arg;           // mode index for SET_MODE, else 0
  uint16_t ttl_s;         // reject if (now_utc - issued_utc_s) > ttl_s and the kart knows UTC; 0 = no limit
  uint32_t issued_utc_s;  // unix seconds at issue (0 = unknown)
};

struct GkBaseInfo {
  GkHeader h;
  int32_t  lat_e7;
  int32_t  lon_e7;
  uint32_t utc_s;         // unix seconds (for display sync), 0 if unknown
};
#pragma pack(pop)

static_assert(sizeof(GkHeader) == 6, "header size");
static_assert(sizeof(GkTelemetry) == 46, "telemetry size changed; update airtime budget and host decoder");
static_assert(sizeof(GkCommandMsg) == 14, "command size");
static_assert(sizeof(GkBaseInfo) == 18, "base info size");

// ---- Helpers ---------------------------------------------------------------
static inline void gkFillHeader(GkHeader& h, uint8_t type, uint8_t src, uint8_t dst, uint8_t seq) {
  h.version = GK_PROTO_VERSION; h.fleet = GK_FLEET_ID; h.type = type; h.src = src; h.dst = dst; h.seq = seq;
}

// Validate a received buffer. Returns the message type or 0 if it is not ours.
static inline uint8_t gkValidate(const uint8_t* buf, size_t len) {
  if (len < sizeof(GkHeader)) return 0;
  const GkHeader* h = (const GkHeader*)buf;
  if (h->version != GK_PROTO_VERSION || h->fleet != GK_FLEET_ID) return 0;
  switch (h->type) {
    case GK_MSG_TELEMETRY: return len == sizeof(GkTelemetry) ? h->type : 0;
    case GK_MSG_COMMAND:   return len == sizeof(GkCommandMsg) ? h->type : 0;
    case GK_MSG_BASE_INFO: return len == sizeof(GkBaseInfo) ? h->type : 0;
  }
  return 0;
}

static inline bool gkAddressedToMe(const GkHeader& h, uint8_t me) {
  return h.dst == me || h.dst == GK_ADDR_ALL;
}

// ---- JSON encoding shared by kart USB output and base USB output -----------
// Produces one line matching firmware/base-tbeam-s3/SERIAL_PROTOCOL.md "tele"
// (type may also be "self" or "fleet" on the kart side). Missing values are
// emitted as null, never as zero.
#include <stdio.h>
static inline const char* gkBool(bool b) { return b ? "true" : "false"; }
static inline int gkTelemetryToJson(char* out, size_t n, const GkTelemetry& t, const char* type,
                                    float rssi, float snr, uint32_t rxMs) {
  char lat[20] = "null", lon[20] = "null", mreq[6] = "null", mact[6] = "null", scale[12] = "null";
  bool fix = t.flags & GK_FLAG_GPS_FIX;
  if (fix) { snprintf(lat, sizeof lat, "%.7f", t.lat_e7 / 1e7); snprintf(lon, sizeof lon, "%.7f", t.lon_e7 / 1e7); }
  if (t.mode_req != GK_MODE_UNKNOWN)    snprintf(mreq, sizeof mreq, "%u", t.mode_req);
  if (t.mode_actual != GK_MODE_UNKNOWN) snprintf(mact, sizeof mact, "%u", t.mode_actual);
  if (t.scale_x1000 != 65535)           snprintf(scale, sizeof scale, "%.3f", t.scale_x1000 / 1000.0);
  return snprintf(out, n,
    "{\"t\":\"%s\",\"kart\":%u,\"seq\":%u,\"lat\":%s,\"lon\":%s,\"fix\":%s,\"gps_age_s\":%u,\"sats\":%u,"
    "\"gps_kph\":%.1f,\"heading\":%.1f,\"volts\":%.1f,\"motor_a\":%.1f,\"batt_a\":%.1f,\"erpm\":%ld,"
    "\"duty\":%.2f,\"esc_c\":%.1f,\"fault\":%u,\"mode_req\":%s,\"mode_actual\":%s,\"scale\":%s,"
    "\"stopped\":%s,\"vesc_link\":%s,\"mode_confirmed\":%s,\"pps\":%s,\"wifi\":%s,\"writes\":%s,"
    "\"last_cmd_seq\":%u,\"cmd_result\":%u,\"ah\":%.2f,\"uptime_s\":%u,\"base_rssi\":%d,"
    "\"rssi\":%.0f,\"snr\":%.1f,\"rx_ms\":%lu}",
    type, t.h.src, t.h.seq, lat, lon, gkBool(fix), t.gps_age_s, t.sats,
    t.gps_kph_x10 / 10.0, t.heading_x10 / 10.0, t.volts_x10 / 10.0, t.motor_a_x10 / 10.0, t.batt_a_x10 / 10.0, (long)t.erpm,
    t.duty_pct / 100.0, t.esc_temp_x10 / 10.0, t.fault, mreq, mact, scale,
    gkBool(t.flags & GK_FLAG_STOPPED), gkBool(t.flags & GK_FLAG_VESC_LINK), gkBool(t.flags & GK_FLAG_MODE_CONFIRMED),
    gkBool(t.flags & GK_FLAG_PPS), gkBool(t.flags & GK_FLAG_WIFI), gkBool(t.flags & GK_FLAG_WRITES_ENABLED),
    t.last_cmd_seq, t.cmd_result, t.ah_x100 / 100.0, t.uptime_s, t.base_rssi,
    (double)rssi, (double)snr, (unsigned long)rxMs);
}

// Unix time from a civil UTC date/time (for GPS). Returns 0 if the date is implausible.
static inline uint32_t gkUnixFromCivil(int y, int m, int d, int hh, int mm, int ss) {
  if (y < 2024 || m < 1 || m > 12 || d < 1 || d > 31) return 0;
  y -= m <= 2;
  const int era = (y >= 0 ? y : y - 399) / 400;
  const unsigned yoe = (unsigned)(y - era * 400);
  const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  const long days = era * 146097L + (long)doe - 719468L;
  return (uint32_t)(days * 86400L + hh * 3600L + mm * 60L + ss);
}
