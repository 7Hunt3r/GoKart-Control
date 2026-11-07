// GoKartProtocol.h — shared LoRa packet format for GoKart Control.
//
// Used by firmware/kart-heltec-v4 (kart side), firmware/base-tbeam-s3 (pit side) and
// firmware/repeater-tbeam-s3. Host test: firmware/tests/test_protocol.py. Everything on the air is one of the packed structs below,
// little-endian, with GkHeader first. Keep packets small: airtime is the scarce
// resource with eight karts sharing one channel.
//
// Status: in use over the air on the bench since 2026-11-05 (kart 2, pit, repeater).
#pragma once
#include <stdint.h>
#include <string.h>

// ---- Protocol identity -----------------------------------------------------
#define GK_PROTO_VERSION   1
#define GK_FLEET_ID        0x4B   // packets with another fleet id are ignored
#define GK_MAX_KARTS       8
#define GK_ADDR_BASE       0      // pit base station
#define GK_ADDR_ALL        0xFF   // broadcast
#define GK_ADDR_REPEATER   0xF0   // first repeater (0xF0..0xF7); heartbeats only, never addressed
// A repeater re-sends a packet it heard with this bit set in the version byte. Receivers
// treat it like the original (dedupe by src+seq); repeaters never relay a relayed packet.
#define GK_HDR_RELAY_BIT   0x80
// Relay timing: a repeater transmits a copy GK_RELAY_DELAY_MS after the end of reception.
// A kart's 23 ms packet plus 25 ms plus a 23 ms copy fits inside its 100 ms slot at 1 Hz, NOT
// inside a 50 ms slot at 2 Hz (see "Slot timing"). A copy the repeater could not start within
// GK_RELAY_LATE_MAX_MS of its due time is dropped: a late beacon copy would mis-sync karts and a
// late telemetry copy would land in the next kart's slot.
#define GK_RELAY_DELAY_MS  25
#define GK_RELAY_LATE_MAX_MS 4

// ---- Radio parameters (identical on both ends) ----------------------------
// US ISM band. These match Colin's installed Heltec bench transmitter
// (firmware/heltec: 915.0 MHz, BW500, SF7, CR4/5, sync 0x12, preamble 8) so both
// frame formats share one channel. SF7/BW500 puts the 47-byte telemetry packet at
// 23.1 ms on air (gkAirtimeUs), which sets the slot plan below. Change SF/BW on every
// board together (the static_asserts below re-check the plan). Range is traded for airtime; raise SF if the track proves too
// big and lower the report rate to match. Not yet declared the final fleet setting.
#define GK_LORA_FREQ_MHZ       915.0
#define GK_LORA_BW_KHZ         500.0
#define GK_LORA_SF             7
#define GK_LORA_CR             5       // 4/5
#define GK_LORA_SYNC_WORD      0x12    // RadioLib "private" sync word, same on SX127x/SX126x
#define GK_LORA_PREAMBLE       8

// ---- Airtime ---------------------------------------------------------------
// LoRa time on air in microseconds for a len-byte payload at the fleet settings (Semtech
// AN1200.13 / SX126x datasheet 6.1.4: explicit header, CRC on, low-data-rate optimisation off,
// which is what RadioLib selects at SF7/BW500). Tsym = 2^SF / BW = 256 us here.
//   47-byte telemetry 23.1 ms, 27-byte beacon 16.7 ms, 22-byte old beacon 14.1 ms,
//   16-byte repeater heartbeat 12.9 ms, 14-byte command 11.6 ms.
static constexpr uint32_t gkTsymUs() { return (uint32_t)((1UL << GK_LORA_SF) * 1000UL / (uint32_t)GK_LORA_BW_KHZ); }
static constexpr uint32_t gkPayloadSymbols(uint32_t len) {
  return 8 + ((8 * len + 28 + 16 > 4 * GK_LORA_SF)
                ? ((8 * len - 4 * GK_LORA_SF + 28 + 16 + 4 * GK_LORA_SF - 1) / (4 * GK_LORA_SF)) * GK_LORA_CR : 0);
}
static constexpr uint32_t gkAirtimeUs(uint32_t len) {
  return (4 * GK_LORA_PREAMBLE + 17) * gkTsymUs() / 4 + gkPayloadSymbols(len) * gkTsymUs();
}

// ---- Slot timing -----------------------------------------------------------
// Karts with a GPS PPS edge align their transmit to the top of the UTC second. Kart k's
// slot opens GK_SLOT_GUARD_MS before (k-1) * slot after the edge; kart 1's opens in the
// tail of the previous round (at 992 ms at 1 Hz, 492/992 ms at 2 Hz). A kart that hears the
// pit beacon but has no PPS aligns to the beacon instead; with neither it free-runs with
// random jitter and simply risks collisions.
//
// Per-second schedule at the fleet radio settings (ms after the edge; airtime from gkAirtimeUs):
//   1 Hz: kart k  (k-1)*100-8 .. +16 late .. +23 packet, repeater copy +25 ms after it (+23)
//         -> each kart's packet AND its repeater copy fit in its 100 ms slot; slots end at 792.
//   2 Hz: kart k  (k-1)*50-8 and 500+(k-1)*50-8, +16 late, +23 packet -> fits a 50 ms slot,
//         BUT a repeater copy (+25 +23) does NOT: it lands on kart k+1's slot. Use rate 1
//         on every kart of a fleet that relies on a repeater (see firmware/README.md).
//   900-992: pit beacon at 905 (+8 late, 17 ms), its repeater copy 25 ms later,
//         the repeater heartbeat right behind it every 5 s; all done by ~980.
//   Pit commands (14 bytes, 12 ms; +25 +12 for a repeater copy) go wherever the base's
//   schedule says no kart that is online, and no beacon, is on the air (gkScheduleBusy);
//   immediately when the base has no time reference.
#define GK_REPORT_PERIOD_MS    1000
#define GK_SLOT_MS             100
#define GK_SLOT_GUARD_MS       8      // early offset so the preamble starts inside the slot
// Latest a kart may start its packet after its slot opened. A loop that is later than this
// skips the round instead of transmitting over the next kart's slot.
#define GK_TX_LATE_MS          16
// Pit beacon: sent at this phase of the base's second, in the tail after all kart slots
// (also clear of the second 2 Hz round, which ends by 881 ms).
#define GK_BEACON_PHASE_MS     905
#define GK_BEACON_LATE_MS      8      // the base skips a beacon rather than start it later than 913 ms
// Legacy fixed command window (still used as the preferred place when the base has PPS and
// the schedule check finds it clear at 1 Hz). Spacing covers one command plus its repeater copy.
#define GK_CMD_WINDOW_START_MS 815
#define GK_CMD_WINDOW_END_MS   895
#define GK_TX_SPACING_MS       50
// A command never waits longer than this for a clear spot in the schedule; after that it goes
// out anyway (a possible collision beats a stalled STOP).
#define GK_CMD_MAX_WAIT_MS     600
// Airtime of a beacon (27 bytes; 22 from old bases) at the fleet settings, rounded to ms.
#define GK_BEACON_AIRTIME_MS   ((gkAirtimeUs(27) + 500) / 1000)
// A kart that hears the beacon but has no GPS PPS aligns its slots to it (sync source 2).
#define GK_SYNC_MAX_AGE_MS     3000
// Optional 2 Hz reporting: two rounds per second with half-width slots. Per-kart runtime
// setting ("rate 1|2" on the kart console); the base estimates each kart's rate from arrivals.
#define GK_SLOT_MS_2HZ         50

// ---- Schedule self-check (compile time) -------------------------------------
// If one of these fires, a radio parameter or packet size change broke the slot plan above.
#define GK_TELE_AIR_MS_CEIL   ((gkAirtimeUs(47) + 999) / 1000)
#define GK_BEACON_AIR_MS_CEIL ((gkAirtimeUs(27) + 999) / 1000)
#define GK_HB_AIR_MS_CEIL     ((gkAirtimeUs(16) + 999) / 1000)
static_assert(gkAirtimeUs(47) == 23104, "airtime formula / radio settings changed: re-check the whole slot plan");
static_assert(GK_TX_LATE_MS + GK_TELE_AIR_MS_CEIL + 2 <= GK_SLOT_MS_2HZ,
              "2 Hz: a late packet would run into the next kart's slot");
static_assert(GK_TX_LATE_MS + 2 * GK_TELE_AIR_MS_CEIL + GK_RELAY_DELAY_MS + 2 <= GK_SLOT_MS,
              "1 Hz: packet + repeater copy no longer fit one slot");
static_assert((GK_MAX_KARTS - 1) * GK_SLOT_MS - GK_SLOT_GUARD_MS + GK_TX_LATE_MS + 2 * GK_TELE_AIR_MS_CEIL + GK_RELAY_DELAY_MS
              <= GK_BEACON_PHASE_MS, "1 Hz: kart 8 (with its repeater copy) runs into the beacon");
static_assert(500 + (GK_MAX_KARTS - 1) * GK_SLOT_MS_2HZ - GK_SLOT_GUARD_MS + GK_TX_LATE_MS + GK_TELE_AIR_MS_CEIL
              <= GK_BEACON_PHASE_MS, "2 Hz: kart 8's second-round packet runs into the beacon");
static_assert(GK_BEACON_PHASE_MS + GK_BEACON_LATE_MS + 2 * GK_BEACON_AIR_MS_CEIL + GK_RELAY_DELAY_MS + 3 + GK_HB_AIR_MS_CEIL
              <= GK_REPORT_PERIOD_MS - GK_SLOT_GUARD_MS, "beacon + repeater copy + heartbeat run into kart 1's slot");
static_assert(2 * gkAirtimeUs(14) + GK_RELAY_DELAY_MS * 1000UL + 1000UL <= GK_TX_SPACING_MS * 1000UL,
              "command spacing shorter than a command plus its repeater copy");

// Where kart `kart` reporting at `rate` (1 or 2 Hz) may be on the air, as offsets in microseconds
// from the second's edge: round n (0..rate-1) occupies [start, start + len) modulo one second.
// `relay` adds the repeater copy. Used by the base to place commands and by the host test.
static inline uint32_t gkSlotStartUs(uint8_t kart, uint8_t rate, uint8_t round) {
  const uint32_t slotUs = (rate == 2 ? GK_SLOT_MS_2HZ : GK_SLOT_MS) * 1000UL;
  const uint32_t periodUs = 1000000UL / (rate == 2 ? 2 : 1);
  return (round * periodUs + (uint32_t)(kart - 1) * slotUs + 1000000UL - GK_SLOT_GUARD_MS * 1000UL) % 1000000UL;
}
static inline uint32_t gkSlotBusyLenUs(bool relay) {
  return GK_TX_LATE_MS * 1000UL + gkAirtimeUs(47) + (relay ? GK_RELAY_DELAY_MS * 1000UL + gkAirtimeUs(47) : 0);
}
// True when [aUs, aUs + aLen) and [bUs, bUs + bLen) overlap on a 1 s circle.
static inline bool gkCircOverlap(uint32_t aUs, uint32_t aLen, uint32_t bUs, uint32_t bLen) {
  uint32_t d = (bUs + 1000000UL - aUs) % 1000000UL;    // b's start, seen from a's start
  if (d < aLen) return true;
  uint32_t e = (aUs + 1000000UL - bUs) % 1000000UL;    // a's start, seen from b's start
  return e < bLen;
}
static inline bool gkKartBusy(uint32_t atUs, uint32_t lenUs, uint8_t kart, uint8_t rate, bool relay) {
  if (kart < 1 || kart > GK_MAX_KARTS) return false;
  for (uint8_t r = 0; r < (rate == 2 ? 2 : 1); r++)
    if (gkCircOverlap(atUs, lenUs, gkSlotStartUs(kart, rate, r), gkSlotBusyLenUs(relay))) return true;
  return false;
}
// The pit's own tail: beacon (+ late start) + repeater copy + heartbeat.
static inline bool gkBeaconBusy(uint32_t atUs, uint32_t lenUs) {
  return gkCircOverlap(atUs, lenUs, GK_BEACON_PHASE_MS * 1000UL,
                       GK_BEACON_LATE_MS * 1000UL + 2 * gkAirtimeUs(27) + GK_RELAY_DELAY_MS * 1000UL + 3000UL + gkAirtimeUs(16));
}

// ---- Message types ---------------------------------------------------------
enum GkMsgType : uint8_t {
  GK_MSG_TELEMETRY = 0x01,   // kart -> everyone, 1 or 2 Hz
  GK_MSG_COMMAND   = 0x02,   // base -> one kart or all
  GK_MSG_BASE_INFO = 0x03,   // base -> everyone once a second: time, sync phase, pit fix, race state
  GK_MSG_REPEATER_INFO = 0x04, // repeater -> base heartbeat every 5 s, right after it relays a beacon
};

// ---- Commands --------------------------------------------------------------
enum GkCommand : uint8_t {
  GK_CMD_NONE     = 0,
  GK_CMD_SET_MODE = 1,   // arg = mode index (0 ECO, 1 NORMAL, 2 SPORT)
  GK_CMD_STOP     = 2,   // ramp motor current scale to zero and hold
  GK_CMD_START    = 3,   // release stop, re-apply requested mode
  GK_CMD_PING     = 4,   // no-op; kart reports the seq back
  GK_CMD_SLOW     = 5,   // cap speed: arg = mph (0 = 5 mph); sets the ERPM limit from the controller's own drivetrain numbers
  GK_CMD_BOOST    = 6,   // timed current-scale override: arg = scale in percent (10..100), 5 s over LoRa; USB `boost SECS SCALE`
  GK_CMD_RACE     = 7,   // race start: hold the kart at zero power now, release (GO) arg seconds after issued_utc_s (1..30); all karts share the pit clock
  GK_CMD_RATE     = 8,   // telemetry rate: arg 1 or 2 Hz, saved on the kart (the pit sends 1 while a repeater is in use: a 50 ms slot cannot hold a packet and its copy)
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
  uint8_t  slow_mph;      // SLOW cap in mph while active, 0 = no SLOW (added 2026-11-06; 46-byte packets lack it)
};

struct GkCommandMsg {
  GkHeader h;             // h.dst = kart id or GK_ADDR_ALL, h.seq = command id
  uint8_t  cmd;           // GkCommand
  uint8_t  arg;           // mode index for SET_MODE, else 0
  uint16_t ttl_s;         // reject if (now_utc - issued_utc_s) > ttl_s and the kart knows UTC; 0 = no limit
  uint32_t issued_utc_s;  // unix seconds at issue (0 = unknown)
};

struct GkBaseInfo {       // pit beacon, once per second at GK_BEACON_PHASE_MS into the base's second
  GkHeader h;
  int32_t  lat_e7;
  int32_t  lon_e7;
  uint32_t utc_s;         // unix seconds at the top of this second (0 if the base has no GPS time)
  uint16_t phase_ms;      // ms into the base's second when this packet started transmitting
  uint8_t  sync_src;      // 0 free-running, 1 GPS PPS, 2 derived from kart slot timing
  uint8_t  karts_online;  // how many karts the pit heard in the last 5 s
  uint8_t  race_state;    // 0 idle, 1 countdown, 2 running, 3 finished (added 2026-11-06; 22-byte beacons lack it)
  uint8_t  pos[4];        // running position per kart, one nibble each: kart k -> (pos[(k-1)/2] >> (((k-1)%2)*4)) & 0xF, 0 = none
};
#define GK_BASEINFO_LEN_V0 22   // beacons from firmware before race_state/pos; still accepted, fields read as 0
static inline uint8_t gkBeaconPos(const struct GkBaseInfo& b, uint8_t kart);

struct GkRepeaterInfo {   // repeater heartbeat; sent with GK_HDR_RELAY_BIT already set so no repeater relays it
  GkHeader h;
  uint16_t heard;         // packets heard since boot (wraps)
  uint16_t relayed;       // packets relayed since boot (wraps)
  uint16_t batt_mv;       // battery millivolts (0 = none / USB only)
  int8_t   pit_rssi;      // how the repeater hears the pit beacon, dBm
  uint8_t  karts_mask;    // bit k-1 set when kart k was heard in the last 10 s
  uint16_t uptime_min;
};
#pragma pack(pop)

static_assert(sizeof(GkHeader) == 6, "header size");
static_assert(sizeof(GkTelemetry) == 47, "telemetry size changed; update airtime budget and host decoder");
#define GK_TELEMETRY_LEN_V0 46   // packets from firmware before slow_mph; still accepted, slow_mph reads as 0
static_assert(sizeof(GkCommandMsg) == 14, "command size");
static_assert(sizeof(GkBaseInfo) == 27, "base info size");
static_assert(sizeof(GkRepeaterInfo) == 16, "repeater info size");

// ---- Helpers ---------------------------------------------------------------
static inline void gkFillHeader(GkHeader& h, uint8_t type, uint8_t src, uint8_t dst, uint8_t seq) {
  h.version = GK_PROTO_VERSION; h.fleet = GK_FLEET_ID; h.type = type; h.src = src; h.dst = dst; h.seq = seq;
}

// Validate a received buffer. Returns the message type or 0 if it is not ours.
static inline uint8_t gkValidate(const uint8_t* buf, size_t len) {
  if (len < sizeof(GkHeader)) return 0;
  const GkHeader* h = (const GkHeader*)buf;
  if ((h->version & 0x7F) != GK_PROTO_VERSION || h->fleet != GK_FLEET_ID) return 0;
  switch (h->type) {
    case GK_MSG_TELEMETRY: return (len == sizeof(GkTelemetry) || len == GK_TELEMETRY_LEN_V0) ? h->type : 0;
    case GK_MSG_COMMAND:   return len == sizeof(GkCommandMsg) ? h->type : 0;
    case GK_MSG_BASE_INFO: return (len == sizeof(GkBaseInfo) || len == GK_BASEINFO_LEN_V0) ? h->type : 0;
    case GK_MSG_REPEATER_INFO: return len == sizeof(GkRepeaterInfo) ? h->type : 0;
  }
  return 0;
}

static inline bool gkIsRelayed(const GkHeader& h) { return (h.version & GK_HDR_RELAY_BIT) != 0; }

static inline uint8_t gkBeaconPos(const GkBaseInfo& b, uint8_t kart) {
  if (kart < 1 || kart > GK_MAX_KARTS) return 0;
  return (b.pos[(kart - 1) / 2] >> (((kart - 1) % 2) * 4)) & 0xF;
}
static inline void gkBeaconSetPos(GkBaseInfo& b, uint8_t kart, uint8_t p) {
  if (kart < 1 || kart > GK_MAX_KARTS) return;
  uint8_t i = (kart - 1) / 2, sh = ((kart - 1) % 2) * 4;
  b.pos[i] = (b.pos[i] & ~(0xF << sh)) | ((p & 0xF) << sh);
}
// Copy a validated beacon (22 or 27 bytes) into b; fields a short packet lacks read as 0.
static inline void gkCopyBeacon(GkBaseInfo& b, const uint8_t* buf, size_t len) {
  memset(&b, 0, sizeof b); memcpy(&b, buf, len < sizeof b ? len : sizeof b);
}
// Copy a validated telemetry packet (46 or 47 bytes) into t; fields a short packet lacks read as 0.
static inline void gkCopyTelemetry(GkTelemetry& t, const uint8_t* buf, size_t len) {
  memset(&t, 0, sizeof t); memcpy(&t, buf, len < sizeof t ? len : sizeof t);
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
// Copy `in` into `out` as the inside of a JSON string: quote/backslash escaped, control and
// non-ASCII bytes dropped. For text that is not ours: SSIDs, pinned strings typed on the console,
// the VESC's hardware name. Always NUL-terminates; truncates to fit.
static inline const char* gkJsonStr(char* out, size_t n, const char* in) {
  size_t o = 0;
  if (!n) return out;
  for (; in && *in && o + 1 < n; in++) {
    unsigned char c = (unsigned char)*in;
    if (c == '"' || c == '\\') { if (o + 2 >= n) break; out[o++] = '\\'; out[o++] = (char)c; }
    else if (c >= 0x20 && c < 0x7F) out[o++] = (char)c;
  }
  out[o] = 0;
  return out;
}
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
    "\"slow_mph\":%u,\"rssi\":%.0f,\"snr\":%.1f,\"rx_ms\":%lu,\"relayed\":%s}",
    type, t.h.src, t.h.seq, lat, lon, gkBool(fix), t.gps_age_s, t.sats,
    t.gps_kph_x10 / 10.0, t.heading_x10 / 10.0, t.volts_x10 / 10.0, t.motor_a_x10 / 10.0, t.batt_a_x10 / 10.0, (long)t.erpm,
    t.duty_pct / 100.0, t.esc_temp_x10 / 10.0, t.fault, mreq, mact, scale,
    gkBool(t.flags & GK_FLAG_STOPPED), gkBool(t.flags & GK_FLAG_VESC_LINK), gkBool(t.flags & GK_FLAG_MODE_CONFIRMED),
    gkBool(t.flags & GK_FLAG_PPS), gkBool(t.flags & GK_FLAG_WIFI), gkBool(t.flags & GK_FLAG_WRITES_ENABLED),
    t.last_cmd_seq, t.cmd_result, t.ah_x100 / 100.0, t.uptime_s, t.base_rssi,
    t.slow_mph, (double)rssi, (double)snr, (unsigned long)rxMs, gkBool(gkIsRelayed(t.h)));
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

// ---- Colin's Heltec bench frame (firmware/heltec, version 0.1.x-bench) ------
// 47 bytes, little-endian: 'G' 'K' 0x01 kartId | u32 bootId | u32 seq | u32 millis |
// u16 sampleAgeMs (65535 none) | u8 flags (bit0 VESC valid, bit1 GPS valid) |
// u8 fault (255 none) | u16 volts*10 | i16 battA*10 | i16 motorA*10 | i32 erpm |
// i16 escC*10 | i32 lat_e7 | i32 lon_e7 | u16 speed m/s*100 (65535 none) |
// u8 sats (255 none) | u16 hdop*100 (65535 none) | u16 fixAgeMs (65535 none).
// Byte 0 is 'G' (0x47) whereas a GoKartProtocol header starts with the version
// byte 0x01, so a receiver can tell the two apart on one channel.
#define GK_BENCH_FRAME_LEN 47
struct GkBenchFrame {
  uint8_t  kartId; uint32_t bootId, seq, millis; uint16_t sampleAgeMs; uint8_t flags, fault;
  float volts, battA, motorA; int32_t erpm; float escC;
  int32_t lat_e7, lon_e7; uint16_t speedCms, hdop100, fixAgeMs; uint8_t sats;
  bool vescValid, gpsValid;
};
static inline uint16_t gkLe16(const uint8_t* b) { return (uint16_t)b[0] | ((uint16_t)b[1] << 8); }
static inline uint32_t gkLe32(const uint8_t* b) { return (uint32_t)b[0] | ((uint32_t)b[1] << 8) | ((uint32_t)b[2] << 16) | ((uint32_t)b[3] << 24); }
static inline bool gkIsBenchFrame(const uint8_t* b, size_t len) { return len == GK_BENCH_FRAME_LEN && b[0] == 'G' && b[1] == 'K' && b[2] == 1; }
static inline bool gkDecodeBenchFrame(const uint8_t* b, size_t len, GkBenchFrame& f) {
  if (!gkIsBenchFrame(b, len)) return false;
  f.kartId = b[3]; f.bootId = gkLe32(b + 4); f.seq = gkLe32(b + 8); f.millis = gkLe32(b + 12);
  f.sampleAgeMs = gkLe16(b + 16); f.flags = b[18]; f.fault = b[19];
  f.vescValid = f.flags & 1; f.gpsValid = f.flags & 2;
  f.volts = gkLe16(b + 20) / 10.0f; f.battA = (int16_t)gkLe16(b + 22) / 10.0f; f.motorA = (int16_t)gkLe16(b + 24) / 10.0f;
  f.erpm = (int32_t)gkLe32(b + 26); f.escC = (int16_t)gkLe16(b + 30) / 10.0f;
  f.lat_e7 = (int32_t)gkLe32(b + 32); f.lon_e7 = (int32_t)gkLe32(b + 36);
  f.speedCms = gkLe16(b + 40); f.sats = b[42]; f.hdop100 = gkLe16(b + 43); f.fixAgeMs = gkLe16(b + 45);
  return true;
}
// JSON "tele" line for a bench frame, same field names as the contract; absent values are null.
static inline int gkBenchToJson(char* out, size_t n, const GkBenchFrame& f, float rssi, float snr, uint32_t rxMs) {
  char lat[20] = "null", lon[20] = "null", volts[12] = "null", ma[12] = "null", ba[12] = "null", erpm[16] = "null", esc[12] = "null", fault[6] = "null", sats[6] = "null", kph[12] = "null", hdop[10] = "null", age[8] = "null";
  if (f.sampleAgeMs != 65535) snprintf(age, sizeof age, "%u", (unsigned)f.sampleAgeMs);
  if (f.gpsValid) { snprintf(lat, sizeof lat, "%.7f", f.lat_e7 / 1e7); snprintf(lon, sizeof lon, "%.7f", f.lon_e7 / 1e7); }
  if (f.vescValid) { snprintf(volts, sizeof volts, "%.1f", f.volts); snprintf(ma, sizeof ma, "%.1f", f.motorA); snprintf(ba, sizeof ba, "%.1f", f.battA); snprintf(erpm, sizeof erpm, "%ld", (long)f.erpm); snprintf(esc, sizeof esc, "%.1f", f.escC); if (f.fault != 255) snprintf(fault, sizeof fault, "%u", f.fault); }
  if (f.sats != 255) snprintf(sats, sizeof sats, "%u", f.sats);
  if (f.speedCms != 65535) snprintf(kph, sizeof kph, "%.1f", f.speedCms / 100.0 * 3.6);
  if (f.hdop100 != 65535) snprintf(hdop, sizeof hdop, "%.2f", f.hdop100 / 100.0);
  return snprintf(out, n,
    "{\"t\":\"tele\",\"bench\":true,\"kart\":%u,\"seq\":%lu,\"boot_id\":%lu,\"lat\":%s,\"lon\":%s,\"fix\":%s,\"gps_age_s\":%u,"
    "\"sats\":%s,\"hdop\":%s,\"gps_kph\":%s,\"heading\":null,\"volts\":%s,\"motor_a\":%s,\"batt_a\":%s,\"erpm\":%s,\"duty\":null,\"esc_c\":%s,"
    "\"fault\":%s,\"mode_req\":null,\"mode_actual\":null,\"scale\":null,\"stopped\":false,\"vesc_link\":%s,\"mode_confirmed\":false,"
    "\"pps\":false,\"wifi\":false,\"writes\":false,\"last_cmd_seq\":0,\"cmd_result\":0,\"ah\":null,\"uptime_s\":%lu,\"base_rssi\":0,"
    "\"sample_age_ms\":%s,\"rssi\":%.0f,\"snr\":%.1f,\"rx_ms\":%lu}",
    f.kartId, (unsigned long)f.seq, (unsigned long)f.bootId, lat, lon, gkBool(f.gpsValid),
    f.fixAgeMs == 65535 ? 255 : (unsigned)(f.fixAgeMs / 1000), sats, hdop, kph, volts, ma, ba, erpm, esc, fault, gkBool(f.vescValid),
    (unsigned long)(f.millis / 1000), age, (double)rssi, (double)snr, (unsigned long)rxMs);
}
