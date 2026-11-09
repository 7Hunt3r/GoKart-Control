#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <string.h>

namespace m5link {
constexpr uint32_t TOKEN_MS = 1000;
constexpr uint32_t PARTIAL_MS = 200;
// Only mode requests are accepted on the display UART; this is not the maintenance console.
struct Request { uint32_t token = 0, id = 0; uint8_t mode = 255; };
inline uint16_t crc16(const char* p, size_t n) {
  uint16_t c = 0;
  for (size_t i=0; i<n; ++i) {
    c ^= uint16_t(uint8_t(p[i])) << 8;
    for (int b=0; b<8; ++b) c = (c & 0x8000) ? uint16_t((c << 1) ^ 0x1021) : uint16_t(c << 1);
  }
  return c;
}
inline bool hex(const char* p, size_t n, uint32_t& v) {
  v=0;
  for (size_t i=0; i<n; ++i) {
    char c=p[i]; unsigned d;
    if(c>='0' && c<='9') d=c-'0';
    else if(c>='a' && c<='f') d=c-'a'+10;
    else if(c>='A' && c<='F') d=c-'A'+10;
    else return false;
    v=(v<<4)|d;
  }
  return true;
}
// Fixed ASCII frame: m5mode TTTTTTTT IIIIIIII M CCCC\n (CRC covers the first 26 bytes).
inline size_t encode(char* out, size_t size, const Request& r) {
  if(size < 34 || !r.token || !r.id || r.mode>2) return 0;
  int n=snprintf(out,size,"m5mode %08lx %08lx %u",(unsigned long)r.token,(unsigned long)r.id,r.mode);
  uint16_t c=crc16(out,size_t(n));
  return size_t(n+snprintf(out+n,size-size_t(n)," %04x\n",c));
}
inline bool decode(const char* line, Request& r) {
  if(strlen(line)!=31 || strncmp(line,"m5mode ",7) || line[15]!=' ' || line[24]!=' ' || line[26]!=' ') return false;
  uint32_t token,id,crc;
  if(!hex(line+7,8,token) || !hex(line+16,8,id) || !hex(line+27,4,crc)) return false;
  if(!token || !id || line[25]<'0' || line[25]>'2' || crc16(line,26)!=crc) return false;
  r.token=token; r.id=id; r.mode=uint8_t(line[25]-'0'); return true;
}
// Lines are bounded and partial/overflow/corrupt lines are discarded through the next newline.
template<size_t N> class Lines {
  char buf[N]{}; size_t len=0; bool dropping=false; uint32_t last=0;
public:
  uint32_t dropped=0;
  const char* feed(char c,uint32_t now) {
    if(len && uint32_t(now-last)>PARTIAL_MS) { len=0; dropping=true; ++dropped; }
    last=now;
    if(c=='\r') return nullptr;
    if(c=='\n') {
      buf[len]=0; bool ok=!dropping && len;
      len=0; dropping=false; return ok?buf:nullptr;
    }
    if(dropping) return nullptr;
    if(uint8_t(c)<32 || len+1>=N) { dropping=true; len=0; ++dropped; return nullptr; }
    buf[len++]=c; return nullptr;
  }
};
// Freshness is a property of an individual request, NEVER a driving-state lease.
class Gate {
public:
  uint32_t token=0, born=0; bool consumed=true;
  void rotate(uint32_t value,uint32_t now) { token=value?value:1; born=now; consumed=false; }
  bool expired(uint32_t now) const { return !token || uint32_t(now-born)>=TOKEN_MS; }
  bool accept(const Request& r,uint32_t now) {
    if(expired(now) || consumed || r.token!=token || !r.id || r.mode>2) return false;
    consumed=true; return true;
  }
};
}
