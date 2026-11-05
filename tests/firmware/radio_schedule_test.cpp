#include <GoKartProtocol.h>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstdio>
static uint32_t airtime(unsigned bytes) {
  double symbol=std::pow(2,GK_LORA_SF)/(GK_LORA_BW_KHZ*1000.0);
  double payload=8+std::ceil((8.0*bytes-4*GK_LORA_SF+28+16)/(4*GK_LORA_SF))*GK_LORA_CR;
  return uint32_t(std::lround((GK_LORA_PREAMBLE+4.25+payload)*symbol*1000000));
}
int main() {
  assert(airtime(sizeof(GkTelemetry))==GK_TELEMETRY_AIRTIME_US);
  assert(airtime(sizeof(GkBaseInfo))==GK_BEACON_AIRTIME_US);
  assert(airtime(sizeof(GkCommandMsg))==GK_COMMAND_AIRTIME_US);
  assert(GK_REPORT_PERIOD_MS==1000/GK_DEFAULT_REPORT_RATE);
  for (unsigned mask=0;mask<256;mask++) {
    uint32_t occupiedUntil=0; unsigned slots=0;
    for (uint32_t round=0;round<2;round++) for(uint8_t kart=1;kart<=8;kart++) {
      uint8_t rate=(mask&(1<<(kart-1)))?2:1;
      uint32_t start=gkSlotOffsetUs(kart)+round*500000UL;
      if(round && rate==1) { assert(!gkSlotOpen(kart,rate,start)); continue; }
      assert(start>=occupiedUntil);
      assert(!gkSlotOpen(kart,rate,start-1));
      for(uint32_t us=0;us<GK_SLOT_GUARD_MS*1000;us++) assert(gkSlotOpen(kart,rate,start+us));
      assert(!gkSlotOpen(kart,rate,start+GK_SLOT_GUARD_MS*1000));
      occupiedUntil=start+GK_SLOT_GUARD_MS*1000-1+airtime(sizeof(GkTelemetry));
      assert(occupiedUntil<=round*500000UL+kart*GK_SLOT_MS*1000UL);
      assert(occupiedUntil<=GK_CMD_WINDOW_START_MS*1000UL || start>=GK_CMD_WINDOW_END_MS*1000UL);
      slots++;
    }
    assert(slots>=8 && slots<=16);
    assert(occupiedUntil<GK_BEACON_PHASE_MS*1000UL);
  }
  for(uint32_t us=0;us<1000000;us+=1000) {
    assert(!gkSlotOpen(0,2,us)); assert(!gkSlotOpen(9,2,us));
    assert(!gkSlotOpen(1,0,us)); assert(!gkSlotOpen(1,3,us));
  }
  // Beacon-derived clocks may be several seconds old: the phase must repeat.
  for(uint8_t kart=1;kart<=8;kart++) for(uint8_t rate=1;rate<=2;rate++)
    assert(gkSlotOpen(kart,rate,3000000UL+gkSlotOffsetUs(kart)));
  assert(GK_BEACON_PHASE_MS*1000UL+airtime(sizeof(GkBaseInfo))<985000UL);
  for(uint32_t phase=0;phase<1000000;phase++) {
    if(gkCommandWindowOpen(phase)) {
      assert(phase>=400000UL);
      assert(phase+airtime(sizeof(GkCommandMsg))+1000UL<=500000UL);
    }
  }
  assert(gkCommandWindowOpen(415000));
  assert(!gkCommandWindowOpen(414999));
  assert(gkCommandWindowOpen(485000-GK_COMMAND_AIRTIME_US-1000));
  assert(!gkCommandWindowOpen(485001-GK_COMMAND_AIRTIME_US-1000));
  assert(!gkCommandWindowOpen(815000)); // Old window collided with kart 7/8.
  puts("radio schedule: 256 mixed-rate fleets, slot boundaries and airtime passed");
}
