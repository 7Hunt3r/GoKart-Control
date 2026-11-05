#pragma once
#include <cstdint>
#include <cstddef>
#include <cmath>
#include <cstring>
#include "vesc_protocol.h"

// Deliberately separate from the normal telemetry firmware. No configuration writes.
namespace bench {
constexpr uint32_t FRESH_MS=350, LEASE_MS=300, ARM_MS=10000, HOST_MS=600;
constexpr int32_t REQUEST_MA=1000, TARGET_ERPM=150, RESUME_ERPM=100, ABORT_ERPM=500;
struct Input {
    bool connected=false, identity=false, baseline=false, usb=false;
    bool haveValues=false,haveAdc=false;
    uint32_t valuesAt=0,adcAt=0,hostAt=0,baselineAt=0;
    vesc::Values values;
    float throttle=0,throttleV=0;
};
enum class State { Idle, Armed, Running };
inline uint32_t age(uint32_t now,uint32_t then){return now-then;}
inline const char* problem(uint32_t now,const Input& i,bool stationary){
    if(!i.connected || !i.identity || !i.baseline)return "identity_or_baseline_not_verified";
    if(stationary && age(now,i.baselineAt)>5000)return "baseline_check_expired";
    if(!i.usb || age(now,i.hostAt)>HOST_MS)return "host_link_lost";
    if(!i.haveValues || !i.haveAdc || age(now,i.valuesAt)>FRESH_MS || age(now,i.adcAt)>FRESH_MS)return "telemetry_stale";
    if(!std::isfinite(i.throttle) || !std::isfinite(i.throttleV) || i.throttle<0 || i.throttle>0.02f || i.throttleV<0.70f || i.throttleV>1.00f)return "throttle_not_neutral";
    if(i.values.fault)return "vesc_fault";
    if(!std::isfinite(i.values.voltage)||i.values.voltage<60 || i.values.voltage>85)return "voltage_outside_bench_range";
    if(!std::isfinite(i.values.escC)||i.values.escC>60 || i.values.escC<0)return "esc_temperature_outside_bench_range";
    if(!std::isfinite(i.values.motorA)||std::fabs(i.values.motorA)>3)return "measured_current_exceeded";
    if(i.values.erpm < -50 || i.values.erpm>ABORT_ERPM)return "rpm_or_direction_limit";
    if(stationary && (i.values.erpm!=0 || std::fabs(i.values.motorA)>0.2f))return "need_stationary_zero_current";
    return nullptr;
}
struct Controller {
    State state=State::Idle;
    const char* reason="idle";
    uint32_t armedAt=0,startedAt=0,duration=0;
    bool dry=false,coasting=false,releasePending=false;
    int32_t requestedMa=0;
    void stop(const char* why){
        if(state==State::Running)releasePending=true;
        state=State::Idle;requestedMa=0;reason=why;
    }
    bool arm(uint32_t now,const Input& i){
        if(state!=State::Idle || releasePending)return false;
        if(const char* p=problem(now,i,true)){reason=p;return false;}
        state=State::Armed;armedAt=now;reason="armed_10_seconds";return true;
    }
    bool start(uint32_t now,const Input& i,uint32_t ms,bool zeroOnly){
        if(state!=State::Armed || age(now,armedAt)>=ARM_MS || (ms!=250 && ms!=5000))return false;
        if(const char* p=problem(now,i,true)){stop(p);return false;}
        state=State::Running;startedAt=now;duration=ms;dry=zeroOnly;coasting=false;
        reason=zeroOnly?"zero_current_dry_run":"pulse_running";tick(now,i);return true;
    }
    void tick(uint32_t now,const Input& i){
        if(state==State::Idle)return;
        if(const char* p=problem(now,i,state==State::Armed)){stop(p);return;}
        if(state==State::Armed){if(age(now,armedAt)>=ARM_MS)stop("arm_expired");return;}
        if(age(now,startedAt)>=duration){stop("pulse_complete_coasting");return;}
        if(i.values.erpm>=TARGET_ERPM)coasting=true;
        if(i.values.erpm<RESUME_ERPM)coasting=false;
        requestedMa=dry||coasting?0:REQUEST_MA;
    }
};
// Packet builders are narrowly typed: no arbitrary payload, RPM/duty mode, current
// argument, indefinite app inhibit, saved-settings write or CAN forwarding API.
inline size_t packet(const uint8_t* p,size_t n,uint8_t* out){
    out[0]=2;out[1]=uint8_t(n);memcpy(out+2,p,n);auto c=vesc::crc(p,n);
    out[n+2]=c>>8;out[n+3]=c;out[n+4]=3;return n+5;
}
inline size_t query(uint8_t id,uint8_t* out){
    if(id!=0 && id!=4 && id!=14 && id!=17 && id!=32)return 0;
    return packet(&id,1,out);
}
inline size_t sampleQueries(uint8_t* out){
    return query(4,out)+query(32,out+6);
}
inline size_t current(bool oneAmp,uint8_t* out){
    static_assert(REQUEST_MA==1000,"update packet encoding if request changes");
    const uint8_t p[]={6,0,0,uint8_t(oneAmp?3:0),uint8_t(oneAmp?0xe8:0)};return packet(p,sizeof(p),out);
}
inline size_t lease(bool enable,uint8_t* out){
    static_assert(LEASE_MS==300,"update packet encoding if lease changes");
    const uint8_t p[]={63,0,0,0,uint8_t(enable?1:0),uint8_t(enable?0x2c:0)};return packet(p,sizeof(p),out);
}
inline bool adc(const uint8_t* p,size_t n,float& level,float& voltage){
    if(n!=17 || p[0]!=32)return false;
    level=vesc::i32(p+1)/1000000.f;voltage=vesc::i32(p+5)/1000000.f;return true;
}
}
