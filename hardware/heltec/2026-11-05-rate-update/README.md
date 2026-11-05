# Kart 1 refresh-rate verification — November 5, 2026

USB-flashed read-only recovery image `0.2.2-bench`; no motor commands, VESC
configuration/firmware writes, resets or detection. The Heltec itself rebooted
for the update. Antenna was previously confirmed attached; serial commands were
`status` and `tx on antenna-confirmed`. TX remains enabled; maintenance is off.

| Measurement | Result |
| --- | --- |
| Window | 24.834 seconds |
| New VESC samples | 121 / 4.872 Hz |
| USB status records | 125 / 4.993 Hz |
| Radio TX completions | 49 / 1.973 Hz |
| BLE receive drops / local TX errors | 0 / 0 |
| VESC identity / freshness | All verified / fresh |
| Motion, current, faults | 0 ERPM, 0 A, fault 0 throughout |
| Longest observed reply / sample age | 243 ms / 352 ms |
| USB reader absent for 5 seconds | Reopened with same boot ID; counters advanced |
| GPS fix / pit receipt / eight-kart RF | Not verified |

`status.jsonl` contains the raw USB records, including reconnect/startup records;
`summary.json` contains the measurement. Rate calculations use the first and last
TX-enabled records after reconnect: counter deltas divided by device uptime delta.
USB rate uses (record count − 1) divided by that same interval.

`manifest.json` hashes the raw trace, source files and flashed binary. The source
snapshot for the telemetry image is commit `11855b7`; later command/base scheduling
changes do not enable writes or slot sync in this installed recovery image. The
binary contains local maintenance credentials and is intentionally not in Git.

The first rate-check attempt stalled USB output after a reader disconnect. The
recovery image's output pump was changed to bounded 64-byte writes with a 1 ms
timeout so the hardware USB driver can service reconnect/backpressure even with
a full queue. A later capture attempt closed before the TX-enable command was
acknowledged and measured no enabled transmissions; the final capture waits for
the acknowledgment before its disconnect check. These failures are not omitted
from the interpretation of the final successful result.

TX completion is local radio evidence, not acknowledgment from Eamon's base.
The recovery image remains unslotted and receive-disabled. All eight scheduled
karts, command delivery, coverage and long-duration stability need separate tests.
