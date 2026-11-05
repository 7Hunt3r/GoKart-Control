#include "bench_control.h"
#include <cassert>
#include <iostream>
using namespace bench;
Input good(uint32_t t){Input i;i.connected=i.identity=i.baseline=i.usb=i.haveValues=i.haveAdc=true;i.valuesAt=i.adcAt=i.hostAt=i.baselineAt=t;i.throttleV=.837f;i.values.voltage=76.2f;i.values.escC=24;return i;}
int main(){
 uint8_t b[16];for(int n=0;n<256;n++)assert(bool(query(n,b))==(n==0||n==4||n==14||n==17||n==32));
 auto n=sampleQueries(b);assert(n==12);vesc::Parser parser;int queries=0;
 parser.feed(b,n,[&](const uint8_t* p,size_t len){assert(len==1 && p[0]==(queries==0?4:32));queries++;});assert(queries==2);
 n=current(true,b);assert(n==10&&b[2]==6&&vesc::i32(b+3)==1000&&vesc::crc(b+2,5)==((b[7]<<8)|b[8]));
 current(false,b);assert(vesc::i32(b+3)==0);
 n=lease(true,b);assert(n==11&&b[2]==63&&b[3]==0&&vesc::i32(b+4)==300);
 lease(false,b);assert(vesc::i32(b+4)==0);
 Controller c;auto i=good(100);assert(!c.start(100,i,250,false));assert(c.arm(100,i));assert(!c.start(100,i,5001,false));assert(c.start(100,i,250,false));assert(c.requestedMa==1000);
 c.tick(349,good(349));assert(c.state==State::Running);c.tick(350,good(350));assert(c.state==State::Idle&&c.releasePending&&c.requestedMa==0);
 assert(!c.arm(351,good(351)));c.releasePending=false;assert(c.arm(351,good(351)));assert(c.start(351,good(351),5000,false));
 i=good(500);i.values.erpm=150;c.tick(500,i);assert(c.requestedMa==0);i.values.erpm=120;c.tick(500,i);assert(c.requestedMa==0);i.values.erpm=99;c.tick(500,i);assert(c.requestedMa==1000);
 i.values.erpm=501;c.tick(500,i);assert(c.state==State::Idle&&c.requestedMa==0);
 for(int x=0;x<10;x++){Controller a;i=good(100);assert(a.arm(100,i));assert(a.start(100,i,5000,false));
  switch(x){case 0:i.connected=false;break;case 1:i.usb=false;break;case 2:i.baseline=false;break;case 3:i.values.fault=1;break;case 4:i.throttle=.03;break;case 5:i.values.motorA=3.1;break;case 6:i.values.erpm=-51;break;case 7:i.values.escC=61;break;case 8:i.throttleV=NAN;break;case 9:i.values.voltage=90;break;}
  a.tick(101,i);assert(a.state==State::Idle&&a.requestedMa==0&&a.releasePending);
 }
 Controller a;i=good(100);assert(a.arm(100,i));assert(a.start(100,i,5000,false));a.tick(451,i);assert(a.state==State::Idle);
 a=Controller{};i=good(100);assert(a.arm(100,i));a.tick(10100,good(10100));assert(a.state==State::Idle);
 a=Controller{};assert(a.arm(0xfffffff0,good(0xfffffff0)));assert(a.start(0xfffffff0,good(0xfffffff0),250,false));a.tick(234,good(234));assert(a.state==State::Idle);
 a=Controller{};i=good(100);assert(a.arm(100,i));assert(a.start(100,i,5000,true));a.tick(5000,good(5000));assert(a.state==State::Running&&a.requestedMa==0);a.tick(5100,good(5100));assert(a.state==State::Idle);
 uint8_t adcRaw[17]={32,0,0,0,0,0,0x0c,0xc5,0xa8};float level,v;assert(adc(adcRaw,17,level,v));assert(level==0&&v>.836&&v<.838);assert(!adc(adcRaw,16,level,v));
 std::cout<<"Bench checks passed: command bounds, arming, timeout/wrap, stale input, disconnect, neutral/fault/current/RPM interlocks, zero-current dry run.\n";
}
