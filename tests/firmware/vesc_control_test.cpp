#include <VescWriteGuard.h>
#include <cassert>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>

uint32_t clockMs = 100;
uint32_t millis() { return clockMs; }
using Bytes = std::vector<uint8_t>;
struct Port : Stream {
  Bytes input;
  std::vector<Bytes> writes;
  bool fail = false;
  int available() override { return int(input.size()); }
  int read() override { int value=input.front(); input.erase(input.begin()); return value; }
  size_t write(const uint8_t* bytes, size_t count) override {
    if (fail) return 0;
    writes.emplace_back(bytes, bytes+count); return count;
  }
};
Bytes load(const char* path) {
  std::ifstream file(path, std::ios::binary); assert(file.good());
  return Bytes(std::istreambuf_iterator<char>(file), {});
}
Bytes payload(const Bytes& frame) {
  size_t header=frame[0]==2?2:3;
  return Bytes(frame.begin()+header,frame.end()-3);
}
void inject(Port& port, VescLink& link, const Bytes& p) {
  Bytes f;
  if(p.size()<256) f={2,uint8_t(p.size())};
  else f={3,uint8_t(p.size()>>8),uint8_t(p.size())};
  f.insert(f.end(),p.begin(),p.end());
  auto crc=VescLink::crc16(p.data(),p.size());
  f.push_back(crc>>8); f.push_back(crc&255); f.push_back(3);
  port.input.insert(port.input.end(),f.begin(),f.end()); link.poll();
}
void setFloat(Bytes& p,int offset,float value) { int32_t at=offset; VescLink::appendFloat32Auto(p.data(),value,&at); }
void setScaled(Bytes& p,int offset,float value) {
  int16_t n=int16_t(lroundf(value*10000)); p[offset]=uint16_t(n)>>8; p[offset+1]=n&255;
}
int motorWrites(const Port& p) {
  int n=0; for(const auto& f:p.writes) if(payload(f)[0]==48) ++n; return n;
}
const char* UUID="2c004c000951343037353739";

int main(int argc,char** argv) {
  assert(argc==4);
  Bytes fw=load(argv[1]), config=load(argv[2]), values=load(argv[3]);
  // Give this controller deliberately non-default limits. All must survive.
  setFloat(config,29,-4321); setFloat(config,33,8765);
  setScaled(config,74,.04); setScaled(config,76,.75);
  setFloat(config,78,5678); setFloat(config,82,-1234);
  setScaled(config,88,.7); setFloat(config,17,42); setFloat(config,21,-11);
  for(float start:{0.0f,.1f,.2f,80.0f/140.0f,1.0f}) {
    Port port; VescLink link(&port); VescWriteGuard guard(link);
    inject(port,link,fw); inject(port,link,values);
    assert(!guard.begin(.6,true,clockMs)); // UUID uncommissioned.
    assert(link.setExpectedUuid(UUID)); assert(link.identityVerified());
    setScaled(config,86,start);
    assert(guard.begin(.6,true,clockMs));
    assert(payload(port.writes.back())[0]==14 && motorWrites(port)==0);
    ++clockMs; inject(port,link,config); guard.service(clockMs);
    assert(guard.snapshotReady());
    Bytes sent=payload(port.writes.back());
    assert(sent.size()==37 && sent[0]==48 && sent[1]==0 && sent[2]==0 && sent[3]==1 && sent[4]==0);
    float requested=fminf(guard.before().currentMaxScale,.6f);
    assert(VescWriteGuard::close(guard.target(),requested));
    int32_t at=5;
    // Wire order is min/max current scale, ERPM, duty, then min/max watts.
    const float wireExpected[]={.7f,requested,-4321,8765,.04f,.75f,-1234,5678};
    for(float x:wireExpected) assert(VescWriteGuard::close(VescLink::getFloat32Auto(sent.data(),&at),x));
    ++clockMs; inject(port,link,Bytes{48}); guard.service(clockMs);
    assert(payload(port.writes.back())[0]==14);
    setScaled(config,86,requested); ++clockMs; inject(port,link,config); guard.service(clockMs);
    assert(!guard.busy() && guard.result()==VescWriteGuard::Applied);
  }
  {
    Port port; VescLink link(&port); VescWriteGuard guard(link);
    link.setExpectedUuid(UUID); inject(port,link,fw); inject(port,link,values);
    assert(guard.begin(.2,true,clockMs)); ++clockMs; inject(port,link,config); guard.service(clockMs);
    ++clockMs; inject(port,link,Bytes{48}); guard.service(clockMs);
    Bytes changed=config; setScaled(changed,86,.2); setScaled(changed,76,.9);
    ++clockMs; inject(port,link,changed); guard.service(clockMs);
    assert(guard.result()==VescWriteGuard::Unknown); // Correct scale cannot hide changed duty.
  }
  {
    Port port; VescLink link(&port); VescWriteGuard guard(link);
    link.setExpectedUuid(UUID); inject(port,link,fw); inject(port,link,values);
    assert(guard.begin(.1,false,clockMs));
    link.markLinkLost(); guard.service(clockMs);
    assert(guard.result()==VescWriteGuard::Unknown && !link.identityVerified());
    inject(port,link,fw); inject(port,link,values); inject(port,link,config);
    guard.service(clockMs); assert(motorWrites(port)==0); // No reconnect replay.
    assert(guard.begin(.1,false,clockMs));
    clockMs+=701; guard.service(clockMs); assert(guard.result()==VescWriteGuard::Unknown);
    assert(motorWrites(port)==0); // A missing fresh reply never uses cached limits.
  }
  {
    Port port; VescLink link(&port); VescWriteGuard guard(link);
    link.setExpectedUuid(UUID); inject(port,link,fw); inject(port,link,values);
    assert(link.identityVerified());
    Bytes bad=fw; bad[1]=7; inject(port,link,bad); assert(!link.identityVerified());
    inject(port,link,fw); bad=fw; bad.resize(5); inject(port,link,bad); assert(!link.identityVerified());
    inject(port,link,fw); assert(link.setExpectedUuid("000000000000000000000000")); assert(!link.identityVerified());
    assert(!link.sendTempLimits(VescTempLimits{})); assert(motorWrites(port)==0);
    assert(!link.setExpectedUuid("not-a-uuid"));
    link.setExpectedUuid(UUID); inject(port,link,fw); inject(port,link,values);
    assert(guard.begin(.1,true,clockMs));
    Bytes shortConfig=config; shortConfig.pop_back(); inject(port,link,shortConfig); guard.service(clockMs);
    assert(motorWrites(port)==0); clockMs+=701; guard.service(clockMs);
    assert(guard.result()==VescWriteGuard::Unknown);
  }
  {
    // Wrap-safe transaction timeout, and no write when the transport fails.
    clockMs=0xfffffff0; Port port; VescLink link(&port); VescWriteGuard guard(link);
    link.setExpectedUuid(UUID); inject(port,link,fw); inject(port,link,values);
    assert(guard.begin(.1,true,clockMs)); clockMs+=10; inject(port,link,config);
    port.fail=true; guard.service(clockMs); assert(guard.result()==VescWriteGuard::Unknown);
    port.fail=false; assert(guard.begin(.1,true,clockMs)); clockMs+=701; guard.service(clockMs);
    assert(guard.result()==VescWriteGuard::Unknown);
  }
  {
    VescCommandHistory history; uint8_t outcome=0;
    history.remember(255,1,100,1); history.remember(255,2,101,4);
    history.remember(255,1,100,2);
    assert(history.lookup(255,1,100,outcome) && outcome==2);
    assert(history.lookup(255,2,101,outcome) && outcome==4);
    assert(!history.lookup(255,2,102,outcome));
  }
  std::cout << "VESC control guards passed: identity, live preservation, non-increasing STOP, verification, disconnect, timeout, malformed reply, transport failure.\n";
}
