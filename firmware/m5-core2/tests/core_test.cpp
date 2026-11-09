#include <cstdio>
#include <cassert>
#include <cstring>
#include <string>
#include <cmath>
#include <M5DisplayLink.h>
#include <GoKartProtocol.h>
#include "DisplayState.h"
using namespace m5dash;
static int checks=0;
#define CHECK(x) do{++checks;if(!(x)){fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x);return 1;}}while(0)
const char* meta=R"({"t":"display","v":1,"kart":1,"boot":11,"token":22,"mode_ready":true,"radio":true,"pit":true,"rate":2,"speed_cal":1.75,"modes":[{"mph":12,"scale":0.55},{"mph":22,"scale":0.8},{"mph":0,"scale":1}]})";
int main(){
  char line[1200];m5link::Request in{0x12345678,0x98765432,2},out;
  auto n=m5link::encode(line,sizeof line,in);CHECK(n==32);line[n-1]=0;
  CHECK(m5link::decode(line,out));CHECK(out.token==in.token&&out.id==in.id&&out.mode==2);
  std::string valid=line;
  for(size_t i=0;i<valid.size();++i){auto bad=valid;bad[i]^=1;CHECK(!m5link::decode(bad.c_str(),out));}
  CHECK(!m5link::decode((valid+" extra").c_str(),out));CHECK(!m5link::decode("mode 2",out));
  m5link::Gate gate;gate.rotate(22,100);CHECK(gate.accept({22,1,1},101));CHECK(!gate.accept({22,1,1},102));
  gate.rotate(23,200);CHECK(!gate.accept({22,2,1},201));CHECK(!gate.accept({23,2,1},1200));
  gate.rotate(24,0xfffffff0);CHECK(gate.accept({24,9,0},10));
  m5link::Lines<40> reader;
  const char* got=nullptr;for(char c:valid+"\n")if(auto p=reader.feed(c,1))got=p;CHECK(got&&valid==got);
  for(char c:std::string(80,'X')+"\n")CHECK(!reader.feed(c,2));
  CHECK(reader.dropped==1);
  reader.feed('m',3);CHECK(!reader.feed('5',250));CHECK(!reader.feed('\n',251));
  for(char c:valid+"\n")if(auto p=reader.feed(c,300))got=p;CHECK(got&&valid==got);
  reader.feed('\0',301);CHECK(!reader.feed('\n',302));

  State s;CHECK(!s.canRequest(1));CHECK(s.ingest(meta,100));CHECK(!s.canRequest(100));
  // Production GoKartProtocol encoder -> actual M5 JSON parser, not a second wire format.
  GkTelemetry t{};t.h={GK_PROTO_VERSION,GK_FLEET_ID,GK_MSG_TELEMETRY,1,GK_ADDR_ALL,17};
  t.flags=GK_FLAG_GPS_FIX|GK_FLAG_VESC_LINK|GK_FLAG_MODE_CONFIRMED|GK_FLAG_WRITES_ENABLED;
  t.lat_e7=410939900;t.lon_e7=-734163700;t.gps_kph_x10=321;t.volts_x10=794;t.batt_a_x10=123;t.esc_temp_x10=351;
  t.mode_actual=1;t.mode_req=1;t.gps_age_s=0;
  gkTelemetryToJson(line,sizeof line,t,"self",0,0,100);CHECK(s.ingest(line,100));
  CHECK(s.self==1&&s.canRequest(110));CHECK(s.karts[1].positionFresh(110));CHECK(std::fabs(s.karts[1].volts-79.4)<0.001);
  CHECK(std::fabs(s.speedCal-1.75)<0.001&&s.cap[0]==12); // Values come from Heltec, not copied defaults.
  CHECK(s.request(2,123,110,line,sizeof line)==32);CHECK(s.pending==123);CHECK(!s.request(1,124,111,line,sizeof line));
  JsonDocument md;deserializeJson(md,meta);md["request"]=122;md["result"]=2;serializeJson(md,line,sizeof line);
  s.ingest(line,120);CHECK(s.pending==123); // unrelated acknowledgement
  md["request"]=123;serializeJson(md,line,sizeof line);s.ingest(line,130);CHECK(!s.pending&&s.result==2);
  CHECK(s.request(0,125,140,line,sizeof line)>0);
  s.tick(900);CHECK(!s.pending&&s.pendingUnknown);CHECK(!s.canRequest(900));
  s.ingest(meta,901);CHECK(s.pending==0);CHECK(s.request(1,126,902,line,sizeof line)>0);
  md["boot"]=12;md["request"]=0;md["result"]=0;serializeJson(md,line,sizeof line);s.ingest(line,903);
  CHECK(s.pending==0&&s.pendingUnknown&&!s.karts[1].seen&&!s.canRequest(904));
  CHECK(!s.ingest("broken json",905));CHECK(!s.ingest("{\"t\":\"self\",\"kart\":9}",906));
  t.h.src=8;gkTelemetryToJson(line,sizeof line,t,"fleet",-70,9,1000);CHECK(s.ingest(line,1000));
  CHECK(s.karts[8].positionFresh(1001));CHECK(!s.karts[8].positionFresh(5001));
  t.flags=0;gkTelemetryToJson(line,sizeof line,t,"fleet",-70,9,1002);s.ingest(line,1002);
  CHECK(!s.karts[8].fix&&!s.karts[8].vesc&&s.karts[8].hasPosition&&std::isnan(s.karts[8].gpsKph));
  Course c;CHECK(s.bounds(c,1100,false).valid);CHECK(!s.bounds(c,1100,true).valid);CHECK(!s.bounds(c,17000,false).valid);
  JsonDocument cd;deserializeJson(cd,R"({"schema":1,"name":"Track","closed":true,"points":[[41.1,-73.4],[41.101,-73.401]]})");
  CHECK(c.load(cd.as<JsonVariantConst>()));CHECK(c.count==2&&s.bounds(c,17000,true).valid);
  double savedLat=c.lat[0];cd["points"][0][0]=999;CHECK(!c.load(cd.as<JsonVariantConst>()));CHECK(c.count==2&&c.lat[0]==savedLat);
  // Rollover-safe local freshness, and missing/null mode or GPS must not become ECO / (0,0).
  State wrap;wrap.ingest(meta,0xfffffff0);t.h.src=1;t.flags=GK_FLAG_VESC_LINK;t.mode_actual=255;
  gkTelemetryToJson(line,sizeof line,t,"self",0,0,0);wrap.ingest(line,0xfffffff0);
  CHECK(wrap.live(100)&&wrap.metadataFresh(100)&&wrap.karts[1].mode==-1&&!wrap.karts[1].hasPosition);
  // Empty telemetry cannot falsely renew a usable VESC/GPS sample.
  wrap.ingest("{\"t\":\"self\",\"kart\":1}",101);CHECK(!wrap.karts[1].vesc&&std::isnan(wrap.karts[1].volts));
  printf("%d C++ assertions passed\n",checks);
}
