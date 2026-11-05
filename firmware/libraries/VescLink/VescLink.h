// VescLink.h — minimal VESC UART client for firmware 6.00.
//
// Implements exactly what the kart needs and nothing that can hurt:
//   * COMM_FW_VERSION       (0)  identity check
//   * COMM_GET_VALUES       (4)  telemetry poll, 6.00 74-byte layout
//   * COMM_GET_MCCONF       (14) read-only: live limit scales for reconciliation
//   * COMM_SET_MCCONF_TEMP  (48) temporary limit override with ack
// store=0 is hard-coded, so the VESC's saved configuration is never written.
// Framing matches tools/vesc/capture.py: 0x02 len payload crc16 0x03.
// Works over any Arduino Stream (hardware UART or a BLE NUS bridge).
#pragma once
#include <Arduino.h>

enum VescComm : uint8_t {
  VESC_COMM_FW_VERSION      = 0,
  VESC_COMM_GET_VALUES      = 4,
  VESC_COMM_GET_MCCONF      = 14,
  VESC_COMM_CUSTOM_APP_DATA = 36,
  VESC_COMM_SET_MCCONF_TEMP = 48,
};

// Signature of the pinned firmware-6.00 motor configuration schema
// (third_party/vesc_tool/6.00/parameters_mcconf.xml). GET_MCCONF replies that
// do not carry it are ignored rather than mis-parsed.
#define VESC_MCCONF_SIGNATURE_6_00  0x2e43a161UL

struct VescValues {
  float    tempMosfet = 0, tempMotor = 0;
  float    motorCurrent = 0, inputCurrent = 0;
  float    duty = 0;
  int32_t  erpm = 0;
  float    inputVoltage = 0;
  float    ampHours = 0, ampHoursCharged = 0, wattHours = 0, wattHoursCharged = 0;
  int32_t  tachometer = 0, tachometerAbs = 0;
  uint8_t  faultCode = 0;
  uint8_t  controllerId = 0;
  bool     timeoutActive = false, killSwitchActive = false;
  uint32_t receivedMs = 0;   // millis() when last updated
};

// Live limits read back from GET_MCCONF. Offsets are fixed for the 6.00 schema
// (computed from the pinned XML; see firmware/README.md).
struct VescActiveLimits {
  float    currentMax = 0, currentMin = 0;         // A
  float    inCurrentMax = 0, inCurrentMin = 0;     // A
  float    absCurrentMax = 0;
  float    currentMinScale = 0, currentMaxScale = 0;
  float    minErpm = 0, maxErpm = 0;
  float    minDuty = 0, maxDuty = 0;
  float    wattMin = 0, wattMax = 0;
  float    batteryCutStart = 0, batteryCutEnd = 0;
  uint8_t  motorPoles = 0;
  float    gearRatio = 0, wheelDiameter = 0;
  uint8_t  batteryCells = 0;
  float    batteryAh = 0;
  uint32_t signature = 0;
  uint32_t receivedMs = 0;
};

// Temporary limit set. Fields map 1:1 onto COMM_SET_MCCONF_TEMP in bldc
// release_6_00 comm/commands.c. Battery current limits are OPTIONAL on the
// wire; when sendInCurrent is false the VESC keeps whatever it has.
struct VescTempLimits {
  float currentMinScale = 1.0f;
  float currentMaxScale = 1.0f;
  float minErpm = -150000.0f;
  float maxErpm =  150000.0f;
  float minDuty = 0.005f;
  float maxDuty = 0.95f;
  float wattMin = -1500000.0f;
  float wattMax =  1500000.0f;
  bool  sendInCurrent = false;
  float inCurrentMin = -100.0f;
  float inCurrentMax =  100.0f;
};

class VescLink {
public:
  explicit VescLink(Stream* port = nullptr) : _port(port) {}
  void setPort(Stream* port) { _port = port; _rxLen = 0; }

  void requestValues();       // GET_VALUES; reply parsed by poll()
  void requestFirmware();     // FW_VERSION; reply parsed into fwMajor/fwMinor/hwName
  void requestMotorConfig();  // GET_MCCONF (read-only); reply parsed into limits()
  // SET_MCCONF_TEMP with ack requested. forwardCan=false, store=false always.
  void sendTempLimits(const VescTempLimits& l);
  // COMM_CUSTOM_APP_DATA: delivered to a LispBM script on the VESC (event-data-rx); ignored if none runs.
  void sendCustomAppData(const uint8_t* data, size_t n);

  // Feed incoming bytes; call often. Returns true if a complete valid packet was handled.
  bool poll();

  const VescValues& values() const { return _values; }
  const VescActiveLimits& limits() const { return _limits; }
  uint32_t lastAckMs() const { return _lastAckMs; }
  bool linkUp(uint32_t maxAgeMs = 1000) const { return _values.receivedMs && (millis() - _values.receivedMs) < maxAgeMs; }
  void markLinkLost() { _values.receivedMs = 0; _limits.receivedMs = 0; _rxLen = 0; }

  uint8_t fwMajor = 0, fwMinor = 0;
  char hwName[24] = {0};
  char uuidHex[25] = {0};      // 12-byte controller UUID from FW_VERSION, lower-case hex
  uint32_t fwSeenMs = 0;
  uint32_t crcErrors = 0, packets = 0, badSignature = 0;

  static uint16_t crc16(const uint8_t* data, size_t len);
  static void appendFloat32Auto(uint8_t* buf, float number, int32_t* index);
  static float getFloat32Auto(const uint8_t* buf, int32_t* index);

private:
  void sendPayload(const uint8_t* payload, uint16_t len);
  void handlePacket(const uint8_t* payload, uint16_t len);
  void parseMotorConfig(const uint8_t* p, uint16_t len);

  Stream* _port;
  VescValues _values;
  VescActiveLimits _limits;
  uint32_t _lastAckMs = 0;
  uint8_t  _rx[640];
  uint16_t _rxLen = 0;
};
