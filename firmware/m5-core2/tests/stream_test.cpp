// Host-only integration: actual telemetry JSON traverses the display's line parser/state.
// The clock models ideal 8N1 serialization, not ESP32 scheduling, RF or GPIO electrical behavior.
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <string>
#include <GoKartProtocol.h>
#include "DisplayState.h"
#include "../../kart-heltec-v4/config.h"

namespace {
void require(bool ok, const char* why) {
  if (!ok) throw std::runtime_error(why);
}

struct Link {
  m5dash::State state;
  m5link::Lines<1152> lines;
  uint64_t bits = 0, bytes = 0, frames = 0, rejected = 0;

  uint32_t now() const { return uint32_t(bits * 1000 / M5_UART_BAUD); }
  void waitUntil(uint32_t ms) {
    bits = std::max(bits, (uint64_t(ms) * M5_UART_BAUD + 999) / 1000);
    state.tick(now());
  }
  void raw(const std::string& data) {
    for (char c : data) {
      bits += 10; // start + eight data + stop bits
      ++bytes;
      if (const char* line = lines.feed(c, now())) {
        if (state.ingest(line, now())) ++frames;
        else ++rejected;
      }
    }
    state.tick(now());
  }
  void line(const std::string& data) { raw(data + '\n'); }
};

std::string telemetry(unsigned id, uint32_t ms, bool gps = true, bool vesc = true) {
  GkTelemetry t{};
  t.h = {GK_PROTO_VERSION, GK_FLEET_ID, GK_MSG_TELEMETRY, uint8_t(id), GK_ADDR_ALL,
         uint8_t(ms / USB_SELF_REPORT_MS)};
  t.flags = GK_FLAG_MODE_CONFIRMED;
  if (gps) t.flags |= GK_FLAG_GPS_FIX;
  if (vesc) t.flags |= GK_FLAG_VESC_LINK;
  t.lat_e7 = 410939900 + int32_t(id * 100 + (ms / 500) % 300);
  t.lon_e7 = -734163700 + int32_t(id * 200);
  t.gps_kph_x10 = 200 + id;
  t.gps_age_s = gps ? 0 : 255;
  t.sats = gps ? 10 : 0;
  t.volts_x10 = 794;
  t.motor_a_x10 = int16_t(100 + id);
  t.batt_a_x10 = int16_t(40 + id);
  t.esc_temp_x10 = int16_t(320 + id);
  t.mode_actual = 1;
  t.mode_req = 1;
  t.scale_x1000 = 750;
  t.erpm = int32_t(3000 + id);
  t.uptime_s = uint16_t(ms / 1000);
  // Nonzero 0.2.5 multiplayer fields must remain harmless extra data on the M5 display.
  t.session = 8;
  t.atk_to = GK_ADDR_ALL;
  t.atk_item = GK_ITEM_LIGHTNING;
  t.atk_seq = 255;
  t.ack_from = 8;
  t.ack_seq = 255;
  t.score = 255;
  char out[1100];
  int n = gkTelemetryToJson(out, sizeof out, t, id == 1 ? "self" : "fleet", -70, 9, ms);
  require(n > 0 && n < int(sizeof out), "production encoder truncated telemetry");
  std::string result(out);
  if (id == 1) {
    // Own USB report extras use these keys; unknown extras must remain compatible.
    result.pop_back();
    result += ",\"hdop\":0.8,\"fix_age_ms\":" + std::to_string(gps ? 0 : 999999);
    result += ",\"gps_hz\":5,\"base_lat\":null,\"base_lon\":null,\"base_fix_age_s\":null,\"race_pos\":0,\"race_state\":0}";
  }
  return result;
}

std::string metadata(uint32_t boot, uint32_t token, bool ready = false,
                     uint32_t request = 0, unsigned result = 0) {
  char out[640];
  int n = snprintf(out, sizeof out,
      "{\"t\":\"display\",\"v\":1,\"kart\":1,\"boot\":%u,\"token\":%u,\"mode_ready\":%s,"
      "\"request\":%u,\"result\":%u,\"radio\":true,\"pit\":true,\"base_rssi\":-70,\"rate\":2,"
      "\"speed_cal\":%.3f,\"drops\":0,\"modes\":[{\"mph\":%u,\"scale\":%.3f},"
      "{\"mph\":%u,\"scale\":%.3f},{\"mph\":%u,\"scale\":%.3f}]}",
      boot, token, gkBool(ready), request, result, double(SPEED_CAL_DEFAULT),
      MODES[0].topMph, double(MODES[0].currentMaxScale), MODES[1].topMph,
      double(MODES[1].currentMaxScale), MODES[2].topMph, double(MODES[2].currentMaxScale));
  require(n > 0 && n < int(sizeof out), "metadata fixture truncated");
  return out;
}

void soak() {
  constexpr uint32_t duration = 600000;
  static_assert(USB_SELF_REPORT_MS == 500, "update soak scheduling for a changed own-report cadence");
  Link link;
  uint64_t maxCycleBytes = 0, expectedFrames = 0;
  double peerGhostMaxX = 0;
  for (uint32_t ms = 0; ms < duration; ms += USB_SELF_REPORT_MS) {
    link.waitUntil(ms);
    // Ten seconds of total cable loss, followed by normal telemetry without any request.
    bool cableLost = ms >= 120000 && ms < 130000;
    bool gpsLost = ms >= 180000 && ms < 200000;
    bool peerLost = ms >= 240000 && ms < 260000;
    uint64_t before = link.bytes;
    if (!cableLost) {
      link.line(telemetry(1, ms, !gpsLost)); ++expectedFrames;
      link.line(metadata(10, ms / 1000 + 1)); ++expectedFrames;
      // Budget the new immediate token-rotation metadata in the same burst as a periodic report.
      if (ms % m5link::TOKEN_MS == 0) {
        link.line(metadata(10, ms / 1000 + 1)); ++expectedFrames;
      }
      // Peers remain at 2 Hz even if the local display reporting interval increases later.
      if (ms % 500 == 0) {
        for (unsigned id = 2; id <= 8; ++id) {
          if (id == 8 && peerLost) continue;
          link.line(telemetry(id, ms, !gpsLost)); ++expectedFrames;
        }
      }
    }
    maxCycleBytes = std::max(maxCycleBytes, link.bytes - before);
    require(link.now() < ms + USB_SELF_REPORT_MS,
            "serialized burst exceeds available UART period; hardware test needed");
    link.waitUntil(ms + USB_SELF_REPORT_MS - 1);
    uint32_t now = link.now();
    require(link.state.pending == 0, "telemetry/reconnect created an unsolicited request");
    require(!link.state.canRequest(now), "read-only metadata enabled mode controls");
    if (!cableLost) {
      require(link.state.live(now) && link.state.metadataFresh(now), "normal cadence went stale");
      require(std::fabs(link.state.karts[1].volts - 79.4f) < 0.001f, "own telemetry changed");
      if (!gpsLost) {
        require(link.state.karts[1].positionFresh(now), "own GPS did not recover");
        for (unsigned id = 2; id <= 8; ++id)
          if (!(id == 8 && peerLost)) require(link.state.karts[id].positionFresh(now), "fleet GPS stale");
      } else {
        require(!link.state.karts[1].positionFresh(now) && std::isnan(link.state.karts[1].gpsKph),
                "GPS loss displayed a fresh position/speed");
      }
    }
    if (ms >= 122000 && ms < 130000)
      require(!link.state.live(now) && !link.state.metadataFresh(now), "cable loss not detected");
    if (ms >= 245000 && ms < 260000)
      require(!link.state.karts[8].online(now), "missing peer stayed online");
    if (ms == 241000) peerGhostMaxX = link.state.bounds(m5dash::Course{}, now, false).maxX;
    if (ms >= 256000 && ms < 260000)
      require(link.state.bounds(m5dash::Course{}, now, false).maxX < peerGhostMaxX - 1.0,
              "expired peer ghost still expands the fleet map");
  }
  require(link.frames == expectedFrames && !link.rejected && !link.lines.dropped,
          "valid stream lost or rejected a frame");
  double worstPeriodPercent = 100.0 * maxCycleBytes * 10 * 1000 / (M5_UART_BAUD * USB_SELF_REPORT_MS);
  printf("M5 stream: PASS, 600 virtual seconds, 8 karts, %llu frames, %llu bytes; "
         "peak scheduled burst %llu bytes / %u ms (%.1f%% ideal 8N1 capacity at %u baud).\n",
         (unsigned long long)link.frames, (unsigned long long)link.bytes,
         (unsigned long long)maxCycleBytes, unsigned(USB_SELF_REPORT_MS), worstPeriodPercent,
         unsigned(M5_UART_BAUD));
}

void recovery() {
  Link link;
  link.waitUntil(100);
  link.line(telemetry(1, 100));
  link.line(metadata(10, 123, true));
  char command[48];
  uint32_t issued = link.now();
  require(link.state.request(2, 99, issued, command, sizeof command) > 0,
          "explicit request did not become pending");
  // Partial/corrupt frames cannot renew state or carry a command acknowledgement.
  uint32_t originalReceived = link.state.karts[1].received;
  link.raw("{\"t\":\"self\"");
  link.waitUntil(link.now() + 250);
  link.raw(",\"kart\":1}\n");
  link.line(std::string(1200, 'x'));
  link.raw(std::string("abc\0def\n", 8));
  link.line("{bad json}");
  require(link.lines.dropped == 3 && link.rejected == 1, "corrupt framing not isolated");
  require(link.state.karts[1].received == originalReceived, "garbage renewed telemetry");
  link.waitUntil(issued + 2000);
  require(!link.state.pending && link.state.pendingUnknown && !link.state.canRequest(link.now()),
          "interrupted request did not become unknown");
  link.line(telemetry(1, link.now()));
  link.line(metadata(10, 124, true));
  require(!link.state.pending && link.state.pendingUnknown,
          "recovering telemetry recreated the pending request");
  require(link.state.karts[1].mode == 1, "requested mode was falsely treated as actual");
  // Even a late APPLIED for the timed-out request cannot erase its unknown outcome.
  link.line(metadata(10, 125, true, 99, GK_RES_APPLIED));
  require(!link.state.pending && link.state.pendingUnknown, "late ack revived old request");
  require(link.state.request(0, 100, link.now(), command, sizeof command) > 0,
          "fresh explicit request after recovery failed");
  link.line(metadata(11, 126, false));
  require(!link.state.pending && link.state.pendingUnknown && !link.state.karts[1].seen,
          "Heltec restart retained old pending state/sample");
  link.line(telemetry(1, link.now(), false, false));
  require(!link.state.karts[1].vesc && !link.state.canRequest(link.now()),
          "controller/GPS loss retained active controls");
  link.line(metadata(11, 127, true));
  link.line(telemetry(1, link.now()));
  require(link.state.canRequest(link.now()) && !link.state.pending,
          "restart recovery requires fresh state and must not queue a command");
  puts("M5 recovery: PASS, cable/GPS/peer loss, corrupt frames, late ack and Heltec restart; no automatic request.");
}

void multiplayerCompatibility() {
  static_assert(GK_TELEMETRY_LEN == 54, "review mixed-version M5 fixtures after radio format changes");
  GkTelemetry packet{};
  gkFillHeader(packet.h, GK_MSG_TELEMETRY, 8, GK_ADDR_ALL, 255);
  packet.flags = GK_FLAG_GPS_FIX | GK_FLAG_VESC_LINK | GK_FLAG_MODE_CONFIRMED;
  packet.lat_e7 = 410939900;
  packet.lon_e7 = -734163700;
  packet.gps_age_s = 0;
  packet.volts_x10 = 794;
  packet.motor_a_x10 = 123;
  packet.batt_a_x10 = 45;
  packet.esc_temp_x10 = 321;
  packet.mode_actual = 2;
  packet.mode_req = 2;
  packet.scale_x1000 = 1000;
  packet.slow_mph = 5;
  packet.session = 8;
  packet.atk_to = GK_ADDR_ALL;
  packet.atk_item = GK_ITEM_LIGHTNING;
  packet.atk_seq = 255;
  packet.ack_from = 1;
  packet.ack_seq = 255;
  packet.score = 255;
  for (size_t length : {size_t(GK_TELEMETRY_LEN_V0), size_t(GK_TELEMETRY_LEN_V1), sizeof packet}) {
    const auto* bytes = reinterpret_cast<const uint8_t*>(&packet);
    require(gkValidate(bytes, length) == GK_MSG_TELEMETRY, "legacy/current radio telemetry rejected");
    GkTelemetry decoded;
    gkCopyTelemetry(decoded, bytes, length);
    char line[GK_TELE_JSON_MAX];
    int n = gkTelemetryToJson(line, sizeof line, decoded, "fleet", -80, 9, 1000);
    require(n > 0 && n < int(sizeof line), "mixed-version telemetry JSON truncated");
    Link receiver;
    receiver.waitUntil(1000);
    receiver.line(line);
    const auto& kart = receiver.state.karts[8];
    require(receiver.frames == 1 && !receiver.rejected && !receiver.lines.dropped,
            "mixed-version telemetry did not traverse the M5 parser");
    require(kart.positionFresh(receiver.now()) && kart.vesc && kart.mode == 2 && kart.confirmed &&
            std::fabs(kart.volts - 79.4f) < 0.001f && std::fabs(kart.motorA - 12.3f) < 0.001f,
            "new game fields changed displayed telemetry");
    require(kart.slow == (length == GK_TELEMETRY_LEN_V0 ? 0 : 5), "legacy SLOW compatibility changed");
    require(!receiver.state.pending && !receiver.state.canRequest(receiver.now()),
            "peer game data enabled/requested a driver mode");
  }

  // Wide numeric fields plus all new game bytes exercise the real production-sized buffers.
  GkTelemetry wide;
  memset(&wide, 0xFF, sizeof wide);
  gkFillHeader(wide.h, GK_MSG_TELEMETRY, 8, GK_ADDR_ALL, 255);
  wide.lat_e7 = INT32_MIN; wide.lon_e7 = INT32_MIN; wide.erpm = INT32_MIN;
  wide.motor_a_x10 = INT16_MIN; wide.batt_a_x10 = INT16_MIN; wide.esc_temp_x10 = INT16_MIN;
  wide.duty_pct = INT8_MIN; wide.base_rssi = INT8_MIN;
  wide.mode_req = 2; wide.mode_actual = 2; wide.scale_x1000 = 65534;
  wide.flags = GK_FLAG_GPS_FIX | GK_FLAG_VESC_LINK | GK_FLAG_MODE_CONFIRMED;
  char fleet[GK_TELE_JSON_MAX];
  int fleetSize = gkTelemetryToJson(fleet, sizeof fleet, wide, "fleet", -127, -20, UINT32_MAX);
  require(fleetSize > 0 && fleetSize < int(sizeof fleet), "wide fleet JSON exceeds production buffer");
  Link large;
  large.waitUntil(10000);
  large.line(fleet);
  require(large.frames == 1 && !large.rejected && !large.lines.dropped,
          "wide fleet JSON rejected by M5 line/state limits");
  require(large.state.karts[8].vesc && !large.state.karts[8].fix,
          "invalid wide coordinates must not become a map position");

  wide.h.src = 1;
  char own[GK_TELE_JSON_MAX + 240]; // usbReport() uses this size, with these extras.
  int baseSize = gkTelemetryToJson(own, sizeof own, wide, "self", 0, 0, UINT32_MAX);
  require(baseSize > 0 && baseSize < int(sizeof own) - 160 && own[baseSize - 1] == '}',
          "self telemetry lost space reserved for USB extras");
  int extraSize = snprintf(own + baseSize - 1, sizeof own - size_t(baseSize) + 1,
      ",\"hdop\":99.99,\"fix_age_ms\":4294967295,\"gps_hz\":5,"
      "\"base_lat\":-90.0000000,\"base_lon\":-180.0000000,\"base_fix_age_s\":29,"
      "\"race_pos\":8,\"race_state\":3}");
  require(extraSize > 0 && size_t(extraSize) < sizeof own - size_t(baseSize) + 1,
          "wide own extras truncated in production-sized buffer");
  size_t ownSize = strlen(own);
  require(ownSize < 1100 && ownSize + 1 < 1152, "own report exceeds M5 JSON/line limits");
  large.line(own);
  require(large.frames == 2 && !large.rejected && !large.lines.dropped && large.state.self == 1,
          "wide self report did not traverse M5 framing/state");
  require(large.state.karts[1].mode == 2 && !large.state.karts[1].fix && !large.state.pending,
          "wide own/game fields changed mode intent or created a GPS fix");

  // These USB game events are not currently mirrored, but must be inert if ever received.
  uint32_t ownReceived = large.state.karts[1].received;
  large.line("{\"t\":\"game\",\"kart\":1,\"session\":8,\"hosting\":true}");
  large.line("{\"t\":\"attack\",\"kart\":1,\"to\":8,\"item\":\"lightning\"}");
  large.line("{\"t\":\"hit\",\"kart\":1,\"from\":8,\"item\":\"shell\",\"applied\":true}");
  require(large.rejected == 3 && large.state.karts[1].received == ownReceived && !large.state.pending,
          "unsupported game events renewed telemetry or requested a mode");
  printf("M5 compatibility: PASS, 46/47/54-byte radio inputs; wide fleet JSON %d/%zu bytes, "
         "self+extras %zu/%zu bytes; multiplayer fields/events remain display-inert.\n",
         fleetSize, sizeof fleet, ownSize, sizeof own);
}

void sourceAge() {
  JsonDocument sample;
  deserializeJson(sample, telemetry(8, 0));
  sample["gps_age_s"] = 2;
  std::string aged; serializeJson(sample, aged);
  Link fleet;
  fleet.waitUntil(10000); fleet.line(aged);
  require(fleet.state.karts[8].positionFresh(fleet.now()), "two-second GPS sample should initially be usable");
  fleet.waitUntil(fleet.now() + 1001);
  require(!fleet.state.karts[8].positionFresh(fleet.now()), "radio reception incorrectly renewed the GPS fix age");

  sample["gps_age_s"] = -1;
  aged.clear(); serializeJson(sample, aged);
  Link invalid; invalid.line(aged);
  require(!invalid.state.karts[8].fix && !invalid.state.karts[8].hasPosition,
          "negative GPS age accepted as a fresh position");

  deserializeJson(sample, telemetry(1, 0));
  sample["fix_age_ms"] = 2900;
  aged.clear(); serializeJson(sample, aged);
  Link own;
  own.waitUntil(10000); own.line(aged);
  require(own.state.karts[1].positionFresh(own.now()), "2.9-second own fix should initially be usable");
  own.waitUntil(own.now() + 101);
  require(!own.state.karts[1].positionFresh(own.now()), "own precise GPS age was reset at receipt");
  puts("M5 GPS age: PASS, source age survives transport; invalid age cannot create a fresh fix.");
}
} // namespace

int main() {
  try { soak(); recovery(); sourceAge(); multiplayerCompatibility(); }
  catch (const std::exception& e) { fprintf(stderr, "M5 stream FAIL: %s\n", e.what()); return 1; }
}
