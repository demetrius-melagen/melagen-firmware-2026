# Unit B `rs422_bridge.py` — Defect Review (Aug 17, 2026)

Target: `/media/Data/Melagen/melagen-firmware-2026/rs422 bridge/rs422_bridge.py` on
Unit B (`melagen2-desktop`, 100.118.164.82). Reviewed against the proven-working
Satlyt pipeline preserved in `satlyt_audit/mirror/` and Unit A test results.

Context: Unit B is a clean slate (no `.satlyt` tree). Goal is a Melagen-only
RS-422 sender for `radfet_logs/` — eliminating Satlyt. TX path proven good
(single test packet landed in `DATA-` on the emulator, Aug 17).

## Defects

| # | Sev | Defect | Detail |
|---|-----|--------|--------|
| 1 | 🔴 | **`encoder()` silently drops all `bytes` payloads** | Indentation bug: `length = len(data)` sits inside `if isinstance(data, str):`, so bytes input keeps `length=0` → emits a 16-byte EMPTY frame with a *valid CRC*. Emulator accepts it into DATA as a well-formed empty message → silent data loss. Proven empirically: `encoder(b"HELLO WORLD")` → `declared_len=0`, payload absent. Only works today because `csv_serial_send` passes `str`. |
| 2 | 🔴 | **Chunk size 0xfff1 (65,521 B)** | The documented *maximum* used as the working size. Unit A testing showed the emulator ingests ~1 msg/~0.7–1.3 s and bursts overflow to DISCARD; proven-good chunk size is **1024 B**. |
| 3 | 🟠 | **Chunks by characters, not bytes** | `chunks(text, 0xfff1)` slices a `str`. Multi-byte UTF-8 → encoded bytes exceed char count → `ValueError` (proven with 40k `é`). Wrong unit; latent for ASCII CSVs. |
| 4 | 🟠 | **Pacing 1.0 s — below proven floor** | Unit A empirics: 100% DATA at ≥1.2 s inter-frame; Satlyt shipped 1.7 s. 1.0 s risks DISCARD spill. |
| 5 | 🟠 | **Hardcoded `flower_bot_training_template.csv`** | Leftover sample; not the RADFET logs; file likely absent (bare except hides it). |
| 6 | 🟡 | **Runs at import time** | Port open + send at module top level; no `if __name__ == "__main__"` → unusable as module/service. |
| 7 | 🟡 | **No `flush()`, `close()` commented out, debug `print(msg)`, bare `except`** | Frames may not drain before exit; failures swallowed with exit code 0. |
| 8 | 🟡 | **No chunk metadata / framing contract** | Raw CSV slices carry no file id/offset/last-chunk → ground side cannot reassemble multi-file streams or detect gaps. (Satlyt's FileChunk protobuf did this.) Decide deliberately. |

## Dropped capabilities to decide on (from Satlyt pipeline)
- ~~**zstd compression**~~ ✅ restored Aug 17 in `mlg_rs422_sender.py` (MLG2). Measured on the
  real closed hour `radfet_2026-08-17_14.csv`: **41,230 B → 5,067 B (8.1:1) → 6 frames
  instead of 42**, i.e. ~75 s of airtime down to ~11 s. Uses `/usr/bin/zstd` at level 19
  via subprocess (`zstandard` module used if importable) — **no new dependency on the unit**.
  - Cost: a compressed blob is all-or-nothing. One missing chunk makes the whole generation
    undecompressable, where raw CSV degrades to "most rows". Mitigated by re-sending every
    cycle (each cycle is an independent generation) and by `total`, which makes truncation
    detectable rather than silent. `--no-compress` restores raw framing for A/B.
- **Hybrid encryption (AES-256-CBC + RSA)**: data now goes over the link in the clear.
- **SHA-256 sidecar**: no ground-side integrity check beyond per-frame CRC16.

## Action plan
- **Phase 1 (correctness — blocking)**: fix #1 (bytes-only encoder, mirror `build_aegis_message` shape); binary file read + byte chunking (#3); chunk 1024 (#2); pacing 1.7 s (#4). ✅ implemented Aug 17
- **Phase 2 (structure)**: `__main__` guard + CLI; flush/close/finally; real logging; non-zero exit on failure. (partially covered by Phase 1 rewrite)
- **Phase 3 (service)**: framing contract (file id/offset/last flag); sent-file ledger + growing-file handling for `radfet_logs/`; `dialout` group; systemd unit + 300 s timer.
- **Phase 4 (validate)**: single packet ✅ → one full hourly file → confirm DATA only, zero DISCARD/ERROR.
