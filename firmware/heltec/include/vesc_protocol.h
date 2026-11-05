#pragma once
#include <cstdint>
#include <cstddef>
#include <cstring>

namespace vesc {
inline uint16_t crc(const uint8_t* p, size_t n) {
    uint16_t c=0;
    for (size_t i=0;i<n;i++) { c ^= uint16_t(p[i])<<8; for(int b=0;b<8;b++) c=(c&0x8000)?(c<<1)^0x1021:c<<1; }
    return c;
}
// Deliberately no generic transmit API: only identity and telemetry requests.
inline bool query(uint8_t command, uint8_t (&out)[6]) {
    if(command!=0 && command!=4) return false;
    uint16_t c=crc(&command,1);
    uint8_t q[6]={2,1,command,uint8_t(c>>8),uint8_t(c),3};
    memcpy(out,q,6); return true;
}
struct Parser {
    uint8_t data[1024]{}; size_t used=0;
    void reset(){used=0;}
    template<class F> void feed(const uint8_t* input,size_t n,F deliver) {
        for(size_t i=0;i<n;i++) {
            if(used==sizeof(data)) reset();
            data[used++]=input[i];
            while(used) {
                if(data[0]!=2 && data[0]!=3) {consume(1);continue;}
                size_t h=data[0]==2?2:3;
                if(used<h) break;
                size_t len=h==2?data[1]:(size_t(data[1])<<8)|data[2];
                if(!len || len>sizeof(data)-6) {consume(1);continue;}
                size_t total=h+len+3;
                if(used<total) break;
                if(data[total-1]==3 && crc(data+h,len)==((uint16_t(data[h+len])<<8)|data[h+len+1])) {
                    deliver(data+h,len); consume(total);
                } else consume(1);
            }
        }
    }
    void consume(size_t n){memmove(data,data+n,used-n);used-=n;}
};
inline int16_t i16(const uint8_t* p){return int16_t((uint16_t(p[0])<<8)|p[1]);}
inline int32_t i32(const uint8_t* p){return int32_t((uint32_t(p[0])<<24)|(uint32_t(p[1])<<16)|(uint32_t(p[2])<<8)|p[3]);}
inline bool matchesIdentity(const uint8_t* p,size_t n,const uint8_t* uuid) {
    if(n<4 || p[0]!=0 || p[1]!=6 || p[2]!=0) return false;
    size_t end=3;while(end<n && p[end]) ++end;
    return end<n && end+13<=n && memcmp(p+end+1,uuid,12)==0;
}
struct Values {
    float voltage=0,batteryA=0,motorA=0,escC=0,duty=0,ampHours=0;
    int32_t erpm=0; uint8_t fault=0,controller=0;
};
inline bool decode(const uint8_t* p,size_t n,Values& v){
    if(n!=74 || p[0]!=4) return false;
    v.escC=i16(p+1)/10.0f; v.motorA=i32(p+5)/100.0f;
    v.batteryA=i32(p+9)/100.0f; v.duty=i16(p+21)/1000.0f;
    v.erpm=i32(p+23);v.voltage=i16(p+27)/10.0f;
    v.ampHours=i32(p+29)/10000.0f;v.fault=p[53];v.controller=p[58];return true;
}
}
