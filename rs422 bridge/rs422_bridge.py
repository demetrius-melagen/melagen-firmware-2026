#!/usr/bin/env python3
"""
MELAGEN RS-422 bridge -- sends a file to the MSC-EC emulator as Aegis messages.

Phase-1 rewrite (Aug 17 2026). Fixes vs the previous version:
  1. encoder silently dropped `bytes` payloads (indentation bug set length=0
     -> valid-CRC EMPTY frames -> silent data loss). Encoder is now bytes-only,
     matching the proven build_aegis_message() shape from the Satlyt mirror.
  2. chunk size 0xfff1 (65,521 B, the protocol MAX) -> 1024 B, the value proven
     against the emulator's ingest rate on Unit A.
  3. files are read in BINARY and chunked by BYTES (was: str chunked by chars,
     which breaks on multi-byte UTF-8 and mis-sizes payloads).
  4. inter-frame pacing 1.0 s -> 1.7 s (Unit A empirics: 100% DATA at >=1.2 s;
     1.7 s is the deployed, validated value).

Aegis Message Format (CSS-FOR-007 sec 6.1):
    SOM(2)=0xA53D | PayloadLen(2) | Timestamp(4) | DeviceID(2) | MessageID(2)
    | Payload(N) | Reserved(2)=0x0000 | CRC16-XMODEM(2, over all prior bytes)
All integers big-endian. Payload max 0xfff1 bytes. Message IDs 0xf000-0xffff
are reserved by Aegis -- do not use.

Usage:
    python3 rs422_bridge.py --file radfet_2026-08-17_18.csv            # send
    python3 rs422_bridge.py --file x.csv --dry-run                     # build only
    python3 rs422_bridge.py --file x.csv --chunk 1024 --delay 1.7 --port /dev/ttyTHS1
"""

import argparse
import binascii
import struct
import sys
import time

MAX_PAYLOAD = 0xFFF1
RESERVED_MSG_ID_MIN = 0xF000  # 0xf000-0xffff reserved by Aegis (heartbeat/reboot/...)
DEFAULT_CHUNK = 1024          # proven against emulator ingest rate (Unit A)
DEFAULT_DELAY = 1.7           # seconds between frames; >=1.2 s proven, 1.7 s deployed


def encode(payload: bytes, device_id: int = 0, message_id: int = 0,
           timestamp: int | None = None) -> bytes:
    """Build one complete Aegis message. Payload must be bytes."""
    if not isinstance(payload, (bytes, bytearray)):
        raise TypeError(f"payload must be bytes, got {type(payload).__name__}")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} exceeds {MAX_PAYLOAD:#x}")
    if RESERVED_MSG_ID_MIN <= message_id <= 0xFFFF:
        raise ValueError(f"message_id {message_id:#x} is in the Aegis-reserved range")
    if timestamp is None:
        timestamp = int(time.time())

    header = struct.pack(">HHIHH", 0xA53D, len(payload), timestamp,
                         device_id, message_id)
    body = header + bytes(payload) + b"\x00\x00"          # reserved footer
    crc = binascii.crc_hqx(body, 0)
    return body + struct.pack(">H", crc)


def iter_chunks(data: bytes, chunk_size: int):
    """Yield byte slices of at most chunk_size."""
    for i in range(0, len(data), chunk_size):
        yield data[i:i + chunk_size]


def send_file(path: str, ser, chunk_size: int = DEFAULT_CHUNK,
              delay: float = DEFAULT_DELAY, device_id: int = 0,
              message_id: int = 0) -> int:
    """Read a file as BYTES, frame it in chunk_size slices, write to serial.
    Returns the number of frames sent."""
    with open(path, "rb") as fh:                # binary -- never decode
        data = fh.read()

    total = (len(data) + chunk_size - 1) // chunk_size if data else 0
    print(f"file    : {path} ({len(data):,} bytes)")
    print(f"chunks  : {total} x <= {chunk_size} B   delay {delay}s/frame")
    est = total * (delay + (chunk_size + 16) * 10 / 115200)
    print(f"est time: {est:.0f}s")

    sent = 0
    for n, chunk in enumerate(iter_chunks(data, chunk_size), start=1):
        frame = encode(chunk, device_id=device_id, message_id=message_id)
        ser.write(frame)
        ser.flush()                              # block until fully transmitted
        sent += 1
        print(f"  [{n}/{total}] sent {len(frame)} B (payload {len(chunk)})", flush=True)
        if n < total:
            time.sleep(delay)
    return sent


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a file to the emulator over RS-422 as Aegis messages.")
    ap.add_argument("--file", required=True, help="file to send (read as binary)")
    ap.add_argument("--port", default="/dev/ttyTHS1")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--chunk", type=int, default=DEFAULT_CHUNK)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    ap.add_argument("--device-id", type=int, default=0)
    ap.add_argument("--message-id", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true", help="build frames, print summary, send nothing")
    args = ap.parse_args()

    if args.dry_run:
        with open(args.file, "rb") as fh:
            data = fh.read()
        frames = [encode(c, args.device_id, args.message_id)
                  for c in iter_chunks(data, args.chunk)]
        print(f"file  : {args.file} ({len(data):,} bytes)")
        print(f"frames: {len(frames)} (first={len(frames[0]) if frames else 0} B, "
              f"last={len(frames[-1]) if frames else 0} B)")
        if frames:
            print(f"first frame hex: {frames[0][:32].hex()}...")
        print("DRY RUN -- nothing sent.")
        return 0

    import serial
    ser = serial.Serial(port=args.port, baudrate=args.baud,
                        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                        bytesize=serial.EIGHTBITS, timeout=1)
    try:
        sent = send_file(args.file, ser, args.chunk, args.delay,
                         args.device_id, args.message_id)
        print(f"DONE: {sent} frame(s) sent on {args.port}")
        return 0
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        ser.close()


if __name__ == "__main__":
    sys.exit(main())
