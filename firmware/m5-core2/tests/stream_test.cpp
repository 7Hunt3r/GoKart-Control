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
  try { soak(); recovery(); sourceAge(); }
  catch (const std::exception& e) { fprintf(stderr, "M5 stream FAIL: %s\n", e.what()); return 1; }
}
