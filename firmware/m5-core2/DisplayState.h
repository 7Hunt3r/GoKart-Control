#pragma once
#include <ArduinoJson.h>
#include <M5DisplayLink.h>
#include <cmath>
#include <algorithm>

namespace m5dash {
inline bool number(JsonVariantConst v) { return (v.is<double>() || v.is<int>()) && std::isfinite(v.as<double>()); }
inline float value(JsonVariantConst v) { return number(v)?v.as<float>():NAN; }
inline bool location(double lat,double lon) { return std::isfinite(lat)&&std::isfinite(lon)&&lat>=-90&&lat<=90&&lon>=-180&&lon<=180; }
struct Kart {
  bool seen=false, fix=false, vesc=false, confirmed=false, stopped=false, writes=false;
  uint32_t received=0, fixReceived=0; bool hasPosition=false;
  double lat=0,lon=0; float gpsKph=NAN,volts=NAN,battA=NAN,motorA=NAN,escC=NAN;
  int mode=-1, fault=-1, sats=0, slow=0; float scale=NAN;
  bool online(uint32_t now) const { return seen && uint32_t(now-received)<=5000; }
  bool positionFresh(uint32_t now) const { return online(now)&&fix&&hasPosition&&uint32_t(now-fixReceived)<=3000; }
};
struct Course {
  static constexpr size_t MAX=128;
  double lat[MAX]{},lon[MAX]{}; size_t count=0; bool closed=false;
  char name[41]="No course loaded";
  bool load(JsonVariantConst d) {
    Course next;
    if(d["schema"]!=1 || !d["points"].is<JsonArrayConst>() || !d["name"].is<const char*>()) return false;
    auto points=d["points"].as<JsonArrayConst>();
    if(points.size()<2 || points.size()>MAX) return false;
    for(auto p:points) {
      if(!p.is<JsonArrayConst>() || p.size()!=2 || !number(p[0]) || !number(p[1])) return false;
      double a=p[0],b=p[1]; if(!location(a,b)) return false;
      next.lat[next.count]=a; next.lon[next.count++]=b;
    }
    snprintf(next.name,sizeof next.name,"%.40s",d["name"].as<const char*>());
    next.closed=d["closed"]|false; *this=next; return true;
  }
};
struct Bounds {
  double lat0=0,lon0=0,minX=0,maxX=0,minY=0,maxY=0; bool valid=false;
  double x(double lon)const { return (lon-lon0)*111320.0*std::cos(lat0*3.141592653589793/180.0); }
  double y(double lat)const { return (lat-lat0)*111320.0; }
  void add(double lat,double lon) {
    if(!valid) {lat0=lat;lon0=lon;valid=true;return;}
    double xx=x(lon),yy=y(lat);minX=std::min(minX,xx);maxX=std::max(maxX,xx);minY=std::min(minY,yy);maxY=std::max(maxY,yy);
  }
};
class State {
public:
  Kart karts[9]; uint8_t self=0;
  uint32_t boot=0, token=0, metaMs=0; bool metaSeen=false, modeReady=false, radio=false,pit=false;
  int rate=0,baseRssi=0; float speedCal=NAN,cap[3]={NAN,NAN,NAN},torque[3]={NAN,NAN,NAN};
  uint32_t pending=0,pendingMs=0; int requested=-1,result=0; bool pendingUnknown=false;
  uint32_t errors=0;
  bool live(uint32_t now) const {return self && karts[self].seen && uint32_t(now-karts[self].received)<=1500;}
  bool metadataFresh(uint32_t now) const { return metaSeen && uint32_t(now-metaMs)<700; }
  bool canRequest(uint32_t now) const {return !pending && live(now)&&metadataFresh(now)&&modeReady&&token&&karts[self].vesc&&!karts[self].stopped;}
  void tick(uint32_t now) {
    if(pending && (!live(now)||!metadataFresh(now)||uint32_t(now-pendingMs)>3000)) {pending=0;pendingUnknown=true;result=5;}
  }
  bool ingest(const char* line,uint32_t now) {
    if(strlen(line)>1100) {++errors;return false;}
    JsonDocument doc;
    if(deserializeJson(doc,line,DeserializationOption::NestingLimit(8))) {++errors;return false;}
    const char* t=doc["t"]|"";
    if(!doc["kart"].is<unsigned>() || doc["kart"].as<unsigned>()<1 || doc["kart"].as<unsigned>()>8) return false;
    unsigned id=doc["kart"];
    if(!strcmp(t,"display")) {
      if(doc["v"]!=1 || !doc["boot"].is<uint32_t>() || !doc["token"].is<uint32_t>()) return false;
      uint32_t nextBoot=doc["boot"];
      if(!nextBoot || !doc["modes"].is<JsonArray>() || doc["modes"].size()!=3) return false;
      if((boot && boot!=nextBoot) || (self && self!=id)) {
        for(auto& k:karts)k=Kart{};
        if(pending){pending=0;pendingUnknown=true;result=5;}
      }
      boot=nextBoot;self=id;token=doc["token"];metaMs=now;metaSeen=true;
      modeReady=doc["mode_ready"]|false;radio=doc["radio"]|false;pit=doc["pit"]|false;
      baseRssi=doc["base_rssi"]|0;rate=doc["rate"]|0;speedCal=value(doc["speed_cal"]);
      for(int i=0;i<3;++i){cap[i]=value(doc["modes"][i]["mph"]);torque[i]=value(doc["modes"][i]["scale"]);}
      if(pending && doc["request"].is<uint32_t>() && doc["request"].as<uint32_t>()==pending) {
        int r=doc["result"]|5;
        if(r>=2 && r<=5){result=r;pending=0;pendingUnknown=r==5;}
      }
      return true;
    }
    bool own=!strcmp(t,"self");
    if(!own && strcmp(t,"fleet")) return false;
    if(own){if(self && self!=id)return false;self=id;}
    else if(id==self)return false;
    auto& k=karts[id];
    k.seen=true;k.received=now;k.vesc=doc["vesc_link"]|false;k.stopped=doc["stopped"]|false;
    k.writes=doc["writes"]|false;k.confirmed=doc["mode_confirmed"]|false;
    k.mode=doc["mode_actual"].is<int>()?doc["mode_actual"].as<int>():-1;
    if(k.mode<0||k.mode>2)k.mode=-1;
    k.volts=value(doc["volts"]);k.battA=value(doc["batt_a"]);k.motorA=value(doc["motor_a"]);k.escC=value(doc["esc_c"]);k.scale=value(doc["scale"]);
    k.fault=doc["fault"].is<int>()?doc["fault"].as<int>():-1;k.sats=doc["sats"]|0;k.slow=doc["slow_mph"]|0;
    k.fix=(doc["fix"]|false)&&number(doc["lat"])&&number(doc["lon"])&&(doc["gps_age_s"]|255)<=2;
    if(own && doc["fix_age_ms"].is<unsigned>() && doc["fix_age_ms"].as<unsigned>()>3000)k.fix=false;
    if(k.fix) {
      double a=doc["lat"],b=doc["lon"];
      if(!location(a,b))k.fix=false;
      else {k.lat=a;k.lon=b;k.hasPosition=true;k.fixReceived=now;}
    }
    k.gpsKph=k.fix?value(doc["gps_kph"]):NAN;
    return true;
  }
  size_t request(uint8_t mode,uint32_t id,uint32_t now,char* out,size_t size) {
    if(!id || mode>2 || !canRequest(now)) return 0;
    size_t n=m5link::encode(out,size,{token,id,mode});
    if(n){pending=id;pendingMs=now;requested=mode;result=1;pendingUnknown=false;token=0;}
    return n;
  }
  Bounds bounds(const Course& course,uint32_t now,bool onlineOnly) const {
    Bounds b;
    for(size_t i=0;i<course.count;++i)b.add(course.lat[i],course.lon[i]);
    for(unsigned i=1;i<=8;++i){const auto& k=karts[i];if(k.hasPosition&&uint32_t(now-k.fixReceived)<=15000&&(!onlineOnly||k.positionFresh(now)))b.add(k.lat,k.lon);}
    return b;
  }
};
}
