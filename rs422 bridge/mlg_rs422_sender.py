#!/usr/bin/env python3
# MELAGEN [AI-deployed, approved by Daniel 2026-08-17]
"""
MELAGEN RS-422 sender -- newest-file, whole-file, ledger-free, zstd-compressed.

Mirrors the melagen-firmware-2026 approach (periodically re-send recent log
file(s) for redundancy) but sends only the SINGLE most recent hourly log.

Each cycle (driven by a systemd timer, ~5s after a RADFET write burst):
  1. pick the most recent radfet_YYYY-MM-DD_HH.csv in WATCH_DIR
  2. read it whole (binary), zstd-compress it (~8:1 on RADFET CSV)
  3. send every byte of the compressed blob as MLG2-framed Aegis messages
  4. done -- no state kept anywhere

There is deliberately NO ledger: re-sending historical/overlapping data is
accepted and gives redundancy against dropped frames.

Framing (payload of each Aegis message):
    MAGIC "MLG2"(4) | flags(1) | name_len(1) | gen(4 BE) | offset(4 BE)
    | total(4 BE) | filename | data
  flags bit0 FILE_COMPLETE -- final chunk, and the file's hour has closed
                              (i.e. the source will never grow again)
  flags bit1 ZSTD          -- the blob this chunk belongs to is a zstd frame
  gen   -- UNCOMPRESSED source size. Groups chunks into one transmission and
           doubles as a length check on the decompressed result.
  offset/total -- position and length within the transmitted blob.

Why `gen` exists
----------------
Ledger-free re-sending is safe for RAW data because the CSV is append-only:
the byte at offset X never changes, so duplicate chunks overwrite harmlessly.
That is NOT true once compressed -- zstd output for a 6 KB input shares no
bytes with the output for the same file at 3 KB. Keying chunks by
(filename, offset) alone would interleave two generations into garbage.
`gen` keys them by (filename, gen, offset) instead, so each cycle's blob
reassembles independently and the ground side keeps the largest complete one.

Trade-off accepted: a compressed blob is all-or-nothing. A capture missing one
chunk yields nothing, whereas raw CSV degrades gracefully to "most rows".
`total` makes that state explicit, and --no-compress restores raw behaviour.

Aegis wrapper: CSS-FOR-007 (SOM 0xA53D, len, ts, dev, msg, payload, resv, CRC16).

Usage:
  mlg_rs422_sender.py --once                  # one send cycle (what the timer runs)
  mlg_rs422_sender.py --once --dry-run        # show what would be sent
  mlg_rs422_sender.py --once --no-compress    # raw MLG2, for A/B against zstd
"""

import argparse
import binascii
try:
    import fcntl                      # Linux (the Nano) -- real inter-process lock
except ImportError:                   # Windows dev box -- tests only, no locking
    fcntl = None
import glob
import os
import re
import struct
import subprocess
import sys
import time

WATCH_DIR   = "/media/Data/Melagen/radfet_logs"
LOCKFILE    = "/tmp/mlg_rs422_sender.lock"
PORT        = "/dev/ttyTHS1"
BAUD        = 115200
DELAY       = 1.7            # s between frames (proven floor 1.2, deployed 1.7)
PAYLOAD_MAX = 1024           # total Aegis payload budget per frame
ZSTD_LEVEL  = 19             # 8.1:1 on a full hour; ~10 ms CPU on 41 KB
ZSTD_BIN    = "zstd"         # /usr/bin/zstd, base Ubuntu -- no new dependency
LOGFILE     = "/media/Data/Melagen/logs/rs422_send.log"
LOG_MAX     = 8 * 1024 * 1024   # rotate at 8 MB, keep 1 old -> 16 MB hard ceiling

MAGIC       = b"MLG2"
HDR_FIXED   = 4 + 1 + 4 + 4 + 4 + 1     # magic, flags, gen, offset, total, name_len
F_COMPLETE  = 0x01
F_ZSTD      = 0x02
HOURLY_RE   = re.compile(r"^radfet_(\d{4}-\d{2}-\d{2}_\d{2})\.csv$")


# ---------------- logging ---------------- #
def log(msg: str, err: bool = False) -> None:
    """Timestamped line. systemd redirects stdout/stderr to LOGFILE; run by
    hand and it just goes to the terminal."""
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}",
          file=sys.stderr if err else sys.stdout, flush=True)


def rotate_log() -> None:
    """Bound the log ourselves -- logrotate is not installed on this unit.
    Runs at cycle start; because the service is Type=oneshot, systemd holds an
    fd on the old inode, so this whole run lands in .1 and the next starts clean."""
    try:
        if os.path.getsize(LOGFILE) >= LOG_MAX:
            os.replace(LOGFILE, LOGFILE + ".1")
    except OSError:
        pass                            # missing file/dir: nothing to rotate


# ---------------- compression ---------------- #
def zstd_compress(data: bytes, level: int = ZSTD_LEVEL) -> bytes:
    """Compress to a standard zstd frame. Prefers the module, falls back to the
    system binary so the flight unit needs no pip-installed dependency."""
    try:
        import zstandard
        return zstandard.ZstdCompressor(level=level).compress(data)
    except ImportError:
        pass
    return subprocess.run([ZSTD_BIN, f"-{level}", "-q", "-c"], input=data,
                          stdout=subprocess.PIPE, check=True).stdout


# ---------------- Aegis wrapper ---------------- #
def aegis(payload: bytes, device_id: int = 0, message_id: int = 0) -> bytes:
    if len(payload) > 0xFFF1:
        raise ValueError("payload too large")
    header = struct.pack(">HHIHH", 0xA53D, len(payload), int(time.time()),
                         device_id, message_id)
    body = header + payload + b"\x00\x00"
    return body + struct.pack(">H", binascii.crc_hqx(body, 0))


# ---------------- MLG2 chunk framing ---------------- #
def mlg2_chunk(name: str, gen: int, offset: int, total: int, data: bytes,
               complete: bool, compressed: bool) -> bytes:
    nb = name.encode()
    if len(nb) > 255:
        raise ValueError("filename too long")
    flags = (F_COMPLETE if complete else 0) | (F_ZSTD if compressed else 0)
    return (MAGIC + bytes([flags, len(nb)])
            + struct.pack(">III", gen, offset, total) + nb + data)


def data_budget(name: str) -> int:
    return PAYLOAD_MAX - (HDR_FIXED + len(name.encode()))


# ---------------- file selection ---------------- #
def newest_log():
    """Most recent hourly log by bucket name. Returns (path, name, closed)."""
    best = None
    for path in glob.glob(os.path.join(WATCH_DIR, "radfet_*.csv")):
        name = os.path.basename(path)
        m = HOURLY_RE.match(name)
        if not m:
            continue                        # ignore daily-format / foreign files
        if best is None or m.group(1) > best[2]:
            best = (path, name, m.group(1))
    if best is None:
        return None, None, False
    path, name, bucket = best
    return path, name, bucket < time.strftime("%Y-%m-%d_%H")


# ---------------- send cycle ---------------- #
def build_frames(name: str, raw: bytes, closed: bool, compress: bool = True):
    """Return (frames, blob, compressed) -- the Aegis frames ready to write."""
    blob = zstd_compress(raw) if compress else raw
    if compress and len(blob) >= len(raw):
        blob, compress = raw, False         # tiny files: compression is a loss

    budget = data_budget(name)
    gen, total = len(raw), len(blob)
    frames = []
    for off in range(0, total, budget):
        sl = blob[off:off + budget]
        last = off + len(sl) >= total
        frames.append((off, len(sl), aegis(mlg2_chunk(
            name, gen, off, total, sl, closed and last, compress))))
    return frames, blob, compress


def run_cycle(dry: bool = False, compress: bool = True) -> int:
    path, name, closed = newest_log()
    if path is None:
        log("no hourly log found")
        return 0

    with open(path, "rb") as fh:            # binary -- never decode
        raw = fh.read()
    if not raw:
        log(f"{name} is empty")
        return 0

    frames, blob, compressed = build_frames(name, raw, closed, compress)
    est = len(frames) * (DELAY + (PAYLOAD_MAX + 16) * 10 / BAUD)
    ratio = f"  zstd {len(raw) / len(blob):.1f}:1" if compressed else "  RAW"
    log(f"file  : {name} ({len(raw):,} B raw -> {len(blob):,} B){ratio}"
        f"{'  [hour closed]' if closed else ''}")
    log(f"frames: {len(frames)} x <= {data_budget(name)} B data   est {est:.0f}s")

    ser = None
    if not dry:
        import serial
        ser = serial.Serial(port=PORT, baudrate=BAUD, timeout=1)

    sent = 0
    try:
        for k, (off, n, frame) in enumerate(frames):
            last = (k == len(frames) - 1)
            tag = " COMPLETE" if closed and last else ""
            if dry:
                log(f"  DRY  off={off} len={n}{tag}")
            else:
                ser.write(frame)
                ser.flush()
                log(f"  sent off={off} len={n}{tag}")
                if not last:
                    time.sleep(DELAY)
            sent += 1
    finally:
        if ser:
            ser.close()
    return sent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one send cycle")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-compress", action="store_true",
                    help="send raw bytes instead of zstd (A/B testing)")
    args = ap.parse_args()

    if not args.once:
        log("nothing to do (use --once)")
        return 2

    rotate_log()

    # single-instance lock: a long send must not overlap the next timer tick
    lock = open(LOCKFILE, "w")
    if fcntl is not None:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("previous cycle still running; skipping")
            return 0
    try:
        n = run_cycle(dry=args.dry_run, compress=not args.no_compress)
        log(f"cycle done: {n} frame(s)")
        return 0
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}", err=True)
        return 1
    finally:
        lock.close()


if __name__ == "__main__":
    sys.exit(main())
