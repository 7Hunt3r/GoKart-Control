# Heltec OLED enable — November 5, 2026

The previous telemetry image did not power or initialize the OLED. The owner
confirmed the small Heltec display was black. `0.2.3-bench` adds a live status
screen while preserving read-only VESC access and the existing radio protocol.

The first implementation stalled inside display-driver startup. A stepped boot
trace reached a successful 0x3c presence probe but stopped when U8g2 initialized.
Releasing the probe bus with `Wire.end()` before `oled.begin()` corrected the
startup: U8g2 reconfigures the pins, and the I²C driver must reattach them. The
final image removes the temporary step diagnostics and starts normally.

USB upload and image hash verification succeeded. Final read-only check:

| Check | Result |
| --- | --- |
| Observation window | 19.919 s |
| OLED controller present | Every status record |
| OLED refreshes | 39 |
| VESC / USB / radio TX rate | 4.82 / 4.97 / 1.958 Hz |
| VESC identity / freshness | Verified, fresh |
| Current / motion / fault | 0 A / 0 ERPM / fault 0 |
| BLE receive drops / TX errors | 0 / 0 |
| End state | TX on; maintenance off; VESC writes disabled |

`status.jsonl` and `summary.json` record the final image only. `manifest.json`
hashes the trace, exact source, dependency pins and flashed binary. The binary
contains local OTA credentials and is intentionally not committed. No VESC
configuration, firmware or motor commands were sent. Only the Heltec rebooted.

Electronic checks confirm the controller acknowledges and display refresh calls
continue. Owner confirmation of visible text was requested after the working
initialization change and is still pending at publication. Pit reception and
motor-command validation are separate from this screen check.
