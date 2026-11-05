#!/bin/bash
# Build / flash the GoKart Control firmware with arduino-cli.
#
#   ./build.sh kart                      compile kart-heltec-v4
#   ./build.sh base                      compile base-tbeam-s3
#   ./build.sh kart-ble                  compile the kart with the BLE VESC transport instead of UART
#   ./build.sh kart upload /dev/cu.usbmodemXXXX
#   ./build.sh base upload /dev/cu.usbmodemXXXX
#   ./build.sh kart ota gokart-1.local [password]      ArduinoOTA over WiFi (board must be on WiFi; see README)
#
# Libraries under firmware/libraries are used in addition to the user's Arduino library folder.
set -euo pipefail
cd "$(dirname "$0")"
TARGET="${1:-kart}"; ACTION="${2:-compile}"; PORT="${3:-}"; OTAPW="${4:-}"; EXTRA=""; BUILD_SUFFIX=""
case "$TARGET" in
  kart)     SKETCH=kart-heltec-v4; FQBN="esp32:esp32:heltec_wifi_lora_32_V4:CDCOnBoot=cdc,PSRAM=disabled,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB" ;;
  kart-ble) SKETCH=kart-heltec-v4; FQBN="esp32:esp32:heltec_wifi_lora_32_V4:CDCOnBoot=cdc,PSRAM=disabled,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB"; EXTRA="--build-property compiler.cpp.extra_flags=-DVESC_TRANSPORT_UART=0"; BUILD_SUFFIX="-ble" ;;
  base) SKETCH=base-tbeam-s3;  FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB,PSRAM=disabled,FlashMode=qio,USBMode=hwcdc" ;;
  *) echo "target must be kart, kart-ble or base"; exit 2 ;;
esac
BUILD="build/$SKETCH$BUILD_SUFFIX"
mkdir -p "$BUILD"
case "$ACTION" in
  compile)
    arduino-cli compile --fqbn "$FQBN" $EXTRA --libraries libraries --build-path "$BUILD" --warnings default "$SKETCH" ;;
  upload)
    [ -n "$PORT" ] || { echo "need a serial port (ls /dev/cu.usbmodem*)"; exit 2; }
    arduino-cli compile --fqbn "$FQBN" $EXTRA --libraries libraries --build-path "$BUILD" "$SKETCH"
    arduino-cli upload --fqbn "$FQBN" --input-dir "$BUILD" -p "$PORT" "$SKETCH" ;;
  ota)
    [ -n "$PORT" ] || { echo "need the board's hostname or IP (e.g. gokart-1.local)"; exit 2; }
    arduino-cli compile --fqbn "$FQBN" $EXTRA --libraries libraries --build-path "$BUILD" "$SKETCH"
    if [ -n "$OTAPW" ]; then arduino-cli upload --fqbn "$FQBN" --input-dir "$BUILD" -p "$PORT" --protocol network --upload-field password="$OTAPW" "$SKETCH"
    else arduino-cli upload --fqbn "$FQBN" --input-dir "$BUILD" -p "$PORT" --protocol network "$SKETCH"; fi ;;
  *) echo "action must be compile, upload or ota"; exit 2 ;;
esac
