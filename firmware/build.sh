#!/bin/bash
# Build / flash the GoKart Control firmware with arduino-cli.
#
#   ./build.sh kart                      compile kart-heltec-v4 (VESC over BLE, fleet default)
#   ./build.sh base                      compile base-tbeam-s3
#   ./build.sh repeater                  compile repeater-tbeam-s3 (plug-anywhere LoRa repeater)
#   ./build.sh kart-uart                 compile the kart with the wired UART VESC transport instead
#   ./build.sh m5                        Core2 v1.5 display (UART, no BLE)
#   ./build.sh m5-demo                   standalone display demo, UART TX disabled
#   ./build.sh kart-m5-readonly         Heltec UART adapter, all VESC writes disabled
#   ./build.sh kart-m5                  Heltec UART adapter with guarded mode requests
#   ./build.sh all                       compile kart, base and repeater (stops at the first failure)
#   ./build.sh test                      host-side protocol tests (python3, no board, no arduino-cli)
#   ./build.sh kart upload /dev/cu.usbmodemXXXX
#   ./build.sh base upload /dev/cu.usbmodemXXXX
#   ./build.sh kart ota gokart-1.local [password]      ArduinoOTA over WiFi (board must be on WiFi; see README)
#
# Libraries under firmware/libraries are used in addition to the user's Arduino library folder.
set -euo pipefail
cd "$(dirname "$0")"
FLAGS=""
TARGET="${1:-kart}"; ACTION="${2:-compile}"; PORT="${3:-}"; OTAPW="${4:-}"; EXTRA=""; BUILD_SUFFIX=""
if [ "$TARGET" = "test" ]; then exec python3 -I tests/test_protocol.py; fi
if [ "$TARGET" = "all" ]; then
  [ "$ACTION" = "compile" ] || { echo "all only supports compile"; exit 2; }
  for t in kart base repeater; do echo "== $t"; "$0" "$t" compile; done; exit 0
fi
case "$TARGET" in
  kart)      SKETCH=kart-heltec-v4; FQBN="esp32:esp32:heltec_wifi_lora_32_V4:CDCOnBoot=cdc,PSRAM=disabled,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB" ;;
  kart-uart) SKETCH=kart-heltec-v4; FQBN="esp32:esp32:heltec_wifi_lora_32_V4:CDCOnBoot=cdc,PSRAM=disabled,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB"; EXTRA="--build-property compiler.cpp.extra_flags=-DVESC_TRANSPORT_UART=1"; BUILD_SUFFIX="-uart" ;;
  repeater) SKETCH=repeater-tbeam-s3; FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB,PSRAM=disabled,FlashMode=qio,USBMode=hwcdc" ;;
  kart-m5|kart-m5-readonly)
    SKETCH=kart-heltec-v4; FQBN="esp32:esp32:heltec_wifi_lora_32_V4:CDCOnBoot=cdc,PSRAM=disabled,FlashSize=16M,PartitionScheme=app3M_fat9M_16MB"
    FLAGS="-DGK_M5_UART=1"; [ "$TARGET" != "kart-m5-readonly" ] || FLAGS="$FLAGS -DENABLE_VESC_WRITES=0"
    BUILD_SUFFIX="-$TARGET" ;;
  m5|m5-demo)
    SKETCH=m5-core2; FQBN="esp32:esp32:m5stack_core2"; BUILD_SUFFIX="-$TARGET"
    [ "$TARGET" != "m5-demo" ] || FLAGS="-DM5_DEMO=1" ;;
  base) SKETCH=base-tbeam-s3;  FQBN="esp32:esp32:esp32s3:CDCOnBoot=cdc,FlashSize=8M,PartitionScheme=default_8MB,PSRAM=disabled,FlashMode=qio,USBMode=hwcdc" ;;
  *) echo "target must be kart, kart-uart, kart-m5, kart-m5-readonly, m5, m5-demo, base, repeater, all or test"; exit 2 ;;
esac
# Keep a single compiler property argument even when the target has multiple flags.
EXTRA_ARGS=(--warnings default)
if [ -n "$FLAGS" ]; then EXTRA_ARGS+=(--build-property "compiler.cpp.extra_flags=$FLAGS"); fi
BUILD="build/$SKETCH$BUILD_SUFFIX"
mkdir -p "$BUILD"
case "$ACTION" in
  compile)
    arduino-cli compile --fqbn "$FQBN" $EXTRA "${EXTRA_ARGS[@]}" --libraries libraries --build-path "$BUILD" "$SKETCH" ;;
  upload)
    [ -n "$PORT" ] || { echo "need a serial port (ls /dev/cu.usbmodem*)"; exit 2; }
    arduino-cli compile --fqbn "$FQBN" $EXTRA "${EXTRA_ARGS[@]}" --libraries libraries --build-path "$BUILD" "$SKETCH"
    arduino-cli upload --fqbn "$FQBN" --input-dir "$BUILD" -p "$PORT" "$SKETCH" ;;
  ota)
    [ -n "$PORT" ] || { echo "need the board's hostname or IP (e.g. gokart-1.local)"; exit 2; }
    arduino-cli compile --fqbn "$FQBN" $EXTRA "${EXTRA_ARGS[@]}" --libraries libraries --build-path "$BUILD" "$SKETCH"
    if [ -n "$OTAPW" ]; then arduino-cli upload --fqbn "$FQBN" --input-dir "$BUILD" -p "$PORT" --protocol network --upload-field password="$OTAPW" "$SKETCH"
    else arduino-cli upload --fqbn "$FQBN" --input-dir "$BUILD" -p "$PORT" --protocol network "$SKETCH"; fi ;;
  *) echo "action must be compile, upload or ota"; exit 2 ;;
esac
