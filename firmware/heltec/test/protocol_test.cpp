#include "vesc_protocol.h"
#include <cassert>
#include <fstream>
#include <iterator>
#include <vector>
#include <iostream>
std::vector<uint8_t> read(const std::string& name){std::ifstream f(name,std::ios::binary);assert(f.good());return {std::istreambuf_iterator<char>(f),{}};}
std::vector<uint8_t> frame(const std::vector<uint8_t>& p){std::vector<uint8_t> b;if(p.size()<256)b={2,uint8_t(p.size())};else b={3,uint8_t(p.size()>>8),uint8_t(p.size())};b.insert(b.end(),p.begin(),p.end());auto c=vesc::crc(p.data(),p.size());b.push_back(c>>8);b.push_back(c);b.push_back(3);return b;}
int main(int argc,char**argv){
 assert(argc==2);std::string base=argv[1];uint8_t q[6];
 for(int i=0;i<256;i++)assert(vesc::query(i,q)==(i==0||i==4));
 vesc::query(0,q);const uint8_t expected[]={2,1,0,0,0,3};assert(!memcmp(q,expected,6));
 const uint8_t uuid[]={0x2c,0,0x4c,0,9,0x51,0x34,0x30,0x37,0x35,0x37,0x39};
 auto ident=read(base+"/firmware.bin");assert(vesc::matchesIdentity(ident.data(),ident.size(),uuid));
 for(size_t i=0;i<4;i++)assert(!vesc::matchesIdentity(ident.data(),i,uuid));
 auto changed=ident;changed[1]=7;assert(!vesc::matchesIdentity(changed.data(),changed.size(),uuid));
 uint8_t wrong[12]{};assert(!vesc::matchesIdentity(ident.data(),ident.size(),wrong));
 auto raw=read(base+"/telemetry.bin");vesc::Values v;assert(vesc::decode(raw.data(),raw.size(),v));
 assert(v.voltage>76.39 && v.voltage<76.41 && v.erpm==0 && v.fault==0 && v.controller==69);
 assert(v.escC>22.29 && v.escC<22.31);assert(!vesc::decode(raw.data(),raw.size()-1,v));
 auto wire=frame(raw);vesc::Parser parser;int got=0;
 auto callback=[&](const uint8_t* p,size_t n){assert(n==raw.size() && !memcmp(p,raw.data(),n));got++;};
 for(auto c:wire)parser.feed(&c,1,callback);assert(got==1);
 auto bad=wire;bad[bad.size()-2]^=1;parser.reset();parser.feed(bad.data(),bad.size(),callback);assert(got==1);
 parser.reset();uint8_t noise[]={255,2,255};parser.feed(noise,sizeof(noise),callback);parser.reset();parser.feed(wire.data(),wire.size(),callback);assert(got==2);
 auto config=read(base+"/motor_configuration.bin");auto longer=frame(config);parser.reset();int longGot=0;
 for(auto c:longer)parser.feed(&c,1,[&](const uint8_t*p,size_t n){assert(n==config.size()&&!memcmp(p,config.data(),n));longGot++;});assert(longGot==1);
 uint8_t oversized[]={3,255,255};parser.reset();parser.feed(oversized,3,callback);parser.feed(wire.data(),wire.size(),callback);assert(got==3);
 std::cout<<"C++ protocol checks passed: only GET commands, UUID gating, baseline decode, fragmentation, CRC and bounds.\n";
}
