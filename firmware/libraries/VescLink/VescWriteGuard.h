#pragma once
#include "VescLink.h"
#include <math.h>

// One explicit command transaction. No retry/replay after failure or reconnect.
// Every step reads current limits, changes only currentMaxScale, then verifies
// all transmitted fields (and that omitted battery-current fields stayed put).
class VescWriteGuard {
public:
  enum Result { Idle, Pending, Applied, Unknown };
  explicit VescWriteGuard(VescLink& link) : v(link) {}
  bool busy() const { return phase != None; }
  Result result() const { return outcome; }
  bool snapshotReady() const { return prepared; }
  const VescActiveLimits& before() const { return prior; }
  float target() const { return expected.currentMaxScale; }

  bool begin(float scale, bool onlyReduce, uint32_t now) {
    if (busy() || !v.identityVerified() || !v.linkUp(1500) ||
        !isfinite(scale) || scale < 0 || scale > 1) return false;
    requested = scale; reducing = onlyReduce; prepared = false;
    revision = v.limitsRevision(); at = now; outcome = Pending; phase = Read;
    v.requestMotorConfig();
    return true;
  }
  void cancel() { if (busy()) outcome = Unknown; phase = None; }
  void service(uint32_t now) {
    if (!busy()) return;
    if (!v.identityVerified() || !v.linkUp(1500) || now - at > 700) { cancel(); return; }
    if (phase == Read && v.limitsRevision() != revision) {
      prior = v.limits();
      if (!valid(prior) || now - prior.receivedMs > 250) { cancel(); return; }
      expected = copyLimits(prior);
      expected.currentMaxScale = reducing ? fminf(requested, prior.currentMaxScale) : requested;
      prepared = true;
      ack = v.lastAckMs();
      if (!v.sendTempLimits(expected)) { cancel(); return; }
      phase = Ack; at = now;
    } else if (phase == Ack && v.lastAckMs() != ack) {
      revision = v.limitsRevision();
      v.requestMotorConfig(); phase = Verify; at = now;
    } else if (phase == Verify && v.limitsRevision() != revision) {
      outcome = matches(v.limits(), expected, prior) ? Applied : Unknown;
      phase = None;
    }
  }
  static VescTempLimits copyLimits(const VescActiveLimits& l) {
    VescTempLimits t;
    t.currentMinScale = l.currentMinScale; t.currentMaxScale = l.currentMaxScale;
    t.minErpm = l.minErpm; t.maxErpm = l.maxErpm;
    t.minDuty = l.minDuty; t.maxDuty = l.maxDuty;
    t.wattMin = l.wattMin; t.wattMax = l.wattMax;
    t.sendInCurrent = false;
    return t;
  }
  static bool valid(const VescActiveLimits& l) {
    const float fields[] = {l.currentMinScale, l.currentMaxScale, l.minErpm, l.maxErpm,
      l.minDuty, l.maxDuty, l.wattMin, l.wattMax, l.inCurrentMin, l.inCurrentMax};
    for (float f : fields) if (!isfinite(f)) return false;
    return l.receivedMs && l.signature == VESC_MCCONF_SIGNATURE_6_00 &&
      l.currentMinScale >= 0 && l.currentMinScale <= 1 &&
      l.currentMaxScale >= 0 && l.currentMaxScale <= 1 &&
      l.minErpm <= l.maxErpm && l.minDuty >= 0 && l.maxDuty <= 1 &&
      l.minDuty <= l.maxDuty && l.wattMin <= l.wattMax && l.inCurrentMin <= l.inCurrentMax;
  }
  static bool close(float a, float b, float absolute = .00011f) {
    return isfinite(a) && isfinite(b) && fabsf(a-b) <= fmaxf(absolute, fabsf(b)*.000001f);
  }
  static bool matches(const VescActiveLimits& l, const VescTempLimits& t, const VescActiveLimits& old) {
    return valid(l) && close(l.currentMaxScale,t.currentMaxScale) &&
      close(l.currentMinScale,t.currentMinScale) && close(l.minErpm,t.minErpm) &&
      close(l.maxErpm,t.maxErpm) && close(l.minDuty,t.minDuty) && close(l.maxDuty,t.maxDuty) &&
      close(l.wattMin,t.wattMin) && close(l.wattMax,t.wattMax) &&
      close(l.inCurrentMin,old.inCurrentMin) && close(l.inCurrentMax,old.inCurrentMax);
  }
private:
  enum Phase { None, Read, Ack, Verify } phase = None;
  VescLink& v;
  Result outcome = Idle;
  uint32_t at = 0, revision = 0, ack = 0;
  float requested = 0;
  bool reducing = false, prepared = false;
  VescActiveLimits prior;
  VescTempLimits expected;
};

// Keep outcomes for recent commands, including rejected overlapping commands.
// A retransmission reports its previous outcome rather than starting a write.
class VescCommandHistory {
public:
  bool lookup(uint8_t src, uint8_t seq, uint32_t issued, uint8_t& result) const {
    for (const auto& e : entries) if (e.valid && e.src==src && e.seq==seq && e.issued==issued) {
      result=e.result; return true;
    }
    return false;
  }
  void remember(uint8_t src, uint8_t seq, uint32_t issued, uint8_t result) {
    for (auto& e : entries) if (e.valid && e.src==src && e.seq==seq && e.issued==issued) { e.result=result; return; }
    entries[next] = Entry{true,src,seq,result,issued}; next=(next+1)%8;
  }
private:
  struct Entry { bool valid=false; uint8_t src=0,seq=0,result=0; uint32_t issued=0; };
  Entry entries[8];
  uint8_t next=0;
};
