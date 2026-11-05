# VESC Tool schema provenance

These two XML schemas are unmodified upstream VESC Tool configuration definitions
for firmware 6.00. They are used for offline decoding, not as device settings.

- Repository: https://github.com/vedderb/vesc_tool
- Pinned commit: `85fc40852541a0cc1f8b6339ff98b1f65a48ed93`
- Paths: `res/config/6.00/parameters_mcconf.xml` and `parameters_appconf.xml`.
- Motor-schema Git blob: `6ef7031cfef259f2f00ddb55a3019ca7e99a1329`.
- App-schema Git blob: `aad35c6d3d1a63f662a984f75a2fd1bf99aae4d4`.
- Both downloaded files' Git blob hashes were verified against upstream metadata.
- License: GPL-3.0-or-later; upstream LICENSE is retained in this directory.
- Upstream author/copyright attribution: Benjamin Vedder and VESC Tool contributors.

Do not confuse XML schema default values with settings read from the kart.
Captured values live under hardware/vesc; the decoder uses SerOrder, transport
types, scales and enum names, and verifies the firmware's schema signature.

Protocol/serialization references inspected: upstream configparams.cpp,
vbytearray.cpp, datatypes.h and commands.cpp; bldc comm/packet.c and comm/commands.c.
The local Python tools provide a limited inspection implementation, not a full
VESC Tool replacement. No upstream firmware or original dashboard source is vendored.
