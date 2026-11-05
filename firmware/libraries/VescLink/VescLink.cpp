#include "VescLink.h"
#include <math.h>

// CRC-16/XMODEM (poly 0x1021, init 0), identical to VESC crc16() and Python crc_hqx(data, 0).
uint16_t VescLink::crc16(const uint8_t* data, size_t len) {
  uint16_t crc = 0;
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (int b = 0; b < 8; b++) crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
  }
  return crc;
}

// bldc buffer_append_float32_auto: portable float encoding used by SET_MCCONF_TEMP.
void VescLink::appendFloat32Auto(uint8_t* buf, float number, int32_t* index) {
  int e = 0;
  float sig = frexpf(number, &e);
  float sig_abs = fabsf(sig);
  uint32_t sig_i = 0;
  if (sig_abs >= 0.5f) {
    sig_i = (uint32_t)((sig_abs - 0.5f) * 2.0f * 8388608.0f);
    e += 126;
  }
  uint32_t res = ((uint32_t)e << 23) | (sig_i & 0x7FFFFF);
  if (sig < 0) res |= 1U << 31;
  buf[(*index)++] = res >> 24; buf[(*index)++] = res >> 16; buf[(*index)++] = res >> 8; buf[(*index)++] = res;
}

float VescLink::getFloat32Auto(const uint8_t* buf, int32_t* index) {
  uint32_t res = ((uint32_t)buf[*index] << 24) | ((uint32_t)buf[*index + 1] << 16) | ((uint32_t)buf[*index + 2] << 8) | buf[*index + 3];
  *index += 4;
  int e = (res >> 23) & 0xFF;
  uint32_t sig_i = res & 0x7FFFFF;
  bool neg = res & (1U << 31);
  float sig = 0.0f;
  if (e != 0 || sig_i != 0) { sig = (float)sig_i / (8388608.0f * 2.0f) + 0.5f; e -= 126; }
  if (neg) sig = -sig;
  return ldexpf(sig, e);
}

static int16_t rd16(const uint8_t* b, int32_t* i) { int16_t v = (b[*i] << 8) | b[*i + 1]; *i += 2; return v; }
static int32_t rd32(const uint8_t* b, int32_t* i) { int32_t v = ((uint32_t)b[*i] << 24) | ((uint32_t)b[*i + 1] << 16) | ((uint32_t)b[*i + 2] << 8) | b[*i + 3]; *i += 4; return v; }
static float   rdF(const uint8_t* b, int32_t i) { return VescLink::getFloat32Auto(b, &i); }
static float   rdS16(const uint8_t* b, int32_t i, float scale) { return rd16(b, &i) / scale; }

void VescLink::sendPayload(const uint8_t* payload, uint16_t len) {
  if (!_port) return;
  uint8_t frame[8 + 300];
  if (len > 256 + 40) return;
  int n = 0;
  if (len < 256) { frame[n++] = 2; frame[n++] = (uint8_t)len; }
  else           { frame[n++] = 3; frame[n++] = len >> 8; frame[n++] = len & 0xFF; }
  memcpy(frame + n, payload, len); n += len;
  uint16_t crc = crc16(payload, len);
  frame[n++] = crc >> 8; frame[n++] = crc & 0xFF; frame[n++] = 3;
  _port->write(frame, n);
}

void VescLink::requestValues()      { uint8_t p = VESC_COMM_GET_VALUES; sendPayload(&p, 1); }
void VescLink::requestFirmware()    { uint8_t p = VESC_COMM_FW_VERSION; sendPayload(&p, 1); }
void VescLink::requestMotorConfig() { uint8_t p = VESC_COMM_GET_MCCONF; sendPayload(&p, 1); }

void VescLink::sendTempLimits(const VescTempLimits& l) {
  uint8_t p[64]; int32_t i = 0;
  p[i++] = VESC_COMM_SET_MCCONF_TEMP;
  p[i++] = 0;   // store: NEVER write flash from this firmware
  p[i++] = 0;   // forward_can
  p[i++] = 1;   // ack: VESC echoes the packet id back so we can confirm
  p[i++] = 0;   // divide_by_controllers
  appendFloat32Auto(p, l.currentMinScale, &i);
  appendFloat32Auto(p, l.currentMaxScale, &i);
  appendFloat32Auto(p, l.minErpm, &i);
  appendFloat32Auto(p, l.maxErpm, &i);
  appendFloat32Auto(p, l.minDuty, &i);
  appendFloat32Auto(p, l.maxDuty, &i);
  appendFloat32Auto(p, l.wattMin, &i);
  appendFloat32Auto(p, l.wattMax, &i);
  if (l.sendInCurrent) {
    appendFloat32Auto(p, l.inCurrentMin, &i);
    appendFloat32Auto(p, l.inCurrentMax, &i);
  }
  sendPayload(p, i);
}

bool VescLink::poll() {
  if (!_port) return false;
  bool handled = false;
  while (_port->available()) {
    uint8_t c = _port->read();
    if (_rxLen < sizeof(_rx)) _rx[_rxLen++] = c; else _rxLen = 0;
    // Try to parse from the start of the buffer; discard until a valid frame appears.
    while (_rxLen >= 2) {
      uint8_t start = _rx[0];
      if (start != 2 && start != 3) { memmove(_rx, _rx + 1, --_rxLen); continue; }
      uint8_t hdr = (start == 2) ? 2 : 3;
      if (_rxLen < hdr) break;
      uint16_t len = (start == 2) ? _rx[1] : ((_rx[1] << 8) | _rx[2]);
      if (len == 0 || len > sizeof(_rx) - 6) { memmove(_rx, _rx + 1, --_rxLen); continue; }
      uint16_t total = hdr + len + 3;
      if (_rxLen < total) break;
      uint16_t crc = (_rx[hdr + len] << 8) | _rx[hdr + len + 1];
      if (_rx[total - 1] != 3 || crc != crc16(_rx + hdr, len)) { crcErrors++; memmove(_rx, _rx + 1, --_rxLen); continue; }
      handlePacket(_rx + hdr, len);
      packets++; handled = true;
      memmove(_rx, _rx + total, _rxLen - total); _rxLen -= total;
    }
  }
  return handled;
}

void VescLink::parseMotorConfig(const uint8_t* p, uint16_t len) {
  // Offsets from payload[0] for the 6.00 schema (482-byte payload). Computed with
  // the same type rules as tools/vesc/decode.py; see firmware/README.md.
  if (len < 482) return;
  uint32_t sig = ((uint32_t)p[1] << 24) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 8) | p[4];
  if (sig != VESC_MCCONF_SIGNATURE_6_00) { badSignature++; return; }
  VescActiveLimits& L = _limits;
  L.signature       = sig;
  L.currentMax      = rdF(p, 9);
  L.currentMin      = rdF(p, 13);
  L.inCurrentMax    = rdF(p, 17);
  L.inCurrentMin    = rdF(p, 21);
  L.absCurrentMax   = rdF(p, 25);
  L.minErpm         = rdF(p, 29);
  L.maxErpm         = rdF(p, 33);
  L.batteryCutStart = rdF(p, 55);
  L.batteryCutEnd   = rdF(p, 59);
  L.minDuty         = rdS16(p, 74, 10000.0f);
  L.maxDuty         = rdS16(p, 76, 10000.0f);
  L.wattMax         = rdF(p, 78);
  L.wattMin         = rdF(p, 82);
  L.currentMaxScale = rdS16(p, 86, 10000.0f);
  L.currentMinScale = rdS16(p, 88, 10000.0f);
  L.motorPoles      = p[452];
  L.gearRatio       = rdF(p, 453);
  L.wheelDiameter   = rdF(p, 457);
  L.batteryCells    = p[462];
  L.batteryAh       = rdF(p, 463);
  L.receivedMs      = millis();
}

void VescLink::handlePacket(const uint8_t* p, uint16_t len) {
  switch (p[0]) {
    case VESC_COMM_FW_VERSION: {
      if (len < 4) return;
      fwMajor = p[1]; fwMinor = p[2];
      size_t n = 0; while (3 + n < len && p[3 + n] && n < sizeof(hwName) - 1) { hwName[n] = p[3 + n]; n++; }
      hwName[n] = 0;
      size_t end = 3; while (end < len && p[end]) end++;          // end of the hw name string
      if (end + 1 + 12 <= len) {
        static const char* hx = "0123456789abcdef";
        for (int i = 0; i < 12; i++) { uuidHex[2 * i] = hx[p[end + 1 + i] >> 4]; uuidHex[2 * i + 1] = hx[p[end + 1 + i] & 15]; }
        uuidHex[24] = 0;
      }
      fwSeenMs = millis();
      break;
    }
    case VESC_COMM_GET_VALUES: {
      // Firmware 6.00 layout, 74 bytes including the command byte (see tools/vesc/decode.py).
      if (len < 74) return;
      int32_t i = 1;
      _values.tempMosfet      = rd16(p, &i) / 10.0f;
      _values.tempMotor       = rd16(p, &i) / 10.0f;
      _values.motorCurrent    = rd32(p, &i) / 100.0f;
      _values.inputCurrent    = rd32(p, &i) / 100.0f;
      i += 8;                                         // id, iq
      _values.duty            = rd16(p, &i) / 1000.0f;
      _values.erpm            = rd32(p, &i);
      _values.inputVoltage    = rd16(p, &i) / 10.0f;
      _values.ampHours        = rd32(p, &i) / 10000.0f;
      _values.ampHoursCharged = rd32(p, &i) / 10000.0f;
      _values.wattHours       = rd32(p, &i) / 10000.0f;
      _values.wattHoursCharged= rd32(p, &i) / 10000.0f;
      _values.tachometer      = rd32(p, &i);
      _values.tachometerAbs   = rd32(p, &i);
      _values.faultCode       = p[i++];
      i += 4;                                         // pid position
      _values.controllerId    = p[i++];
      i += 6;                                         // mosfet temps 1..3
      i += 8;                                         // vd, vq
      uint8_t status = (i < len) ? p[i] : 0;
      _values.timeoutActive    = status & 1;
      _values.killSwitchActive = status & 2;
      _values.receivedMs = millis();
      break;
    }
    case VESC_COMM_GET_MCCONF:
      parseMotorConfig(p, len);
      break;
    case VESC_COMM_SET_MCCONF_TEMP:
      _lastAckMs = millis();
      break;
    default:
      break;
  }
}
