#!/usr/bin/env python3
"""
Ground-side decoder for MELAGEN MLG1/MLG2 chunks inside Aegis captures.

Parses any mix of captures (DATA-*.msg, DISCARD-*.bin, ...), pools the MELAGEN
payloads across all inputs, and reconstructs the original files. Head/tail
spills that land in DISCARD are intact Aegis frames, so pooling a DATA capture
with its DISCARD sibling fills gaps automatically.

Wire formats
------------
MLG2 (current -- compressed, generation-keyed):
    "MLG2"(4) | flags(1) | name_len(1) | gen(4 BE) | offset(4 BE)
    | total(4 BE) | filename | data
      flags bit0 FILE_COMPLETE  final chunk and the source hour has closed
      flags bit1 ZSTD           the blob is a zstd frame
      gen    UNCOMPRESSED source size; groups chunks into one transmission
      total  full blob length; completeness is provable without the last chunk

MLG1 (legacy -- raw only):
    "MLG1"(4) | flags(1) | name_len(1) | offset(8 BE) | filename | data
  Treated as gen=0, total unknown, uncompressed.

Why generations matter
----------------------
The sender re-sends the whole newest log every cycle. For RAW data that is
harmless -- an append-only CSV has the same byte at offset X forever, so
duplicates overwrite cleanly. Compressed, it is not: zstd output for a 6 KB
input shares no bytes with the output for the same file at 3 KB. Chunks are
therefore pooled by (filename, gen, offset), each generation is reassembled
independently, and the largest intact generation wins.

Reports per capture: frame count, CRC failures, unrecognised payloads (hex
preview). Reports per file: every generation seen, chunks, bytes, gaps,
conflicts, FILE_COMPLETE, and for .csv output a validation summary (header, row
counts, timestamp monotonicity, and the rows bracketing each gap).

Holes in raw output are zero-filled so byte offsets stay true; --clean also
writes <name>.clean.csv holding only intact rows. A compressed generation with
any gap is undecompressable and is reported as such -- there is no partial read.

Exit code 0 only when every file has an intact, gap-free, FILE_COMPLETE
generation (automation-friendly).

Usage:
  python mlg_reassemble.py DATA-*.msg [DISCARD-*.bin ...] --outdir out/
                           [--clean] [--all-gens] [--json]
"""
import argparse
import binascii
import io
import json
import os
import struct
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MAGIC1 = b"MLG1"
MAGIC2 = b"MLG2"
F_COMPLETE = 0x01
F_ZSTD = 0x02


# ---------------------------- decompression ---------------------------- #
def zstd_decompress(blob: bytes) -> bytes:
    """Prefers the zstandard module, falls back to the system binary.
    stream_reader is used because frames written from a pipe carry no
    content-size header, which the one-shot .decompress() requires."""
    try:
        import zstandard as zs
        return zs.ZstdDecompressor().stream_reader(io.BytesIO(blob)).read()
    except ImportError:
        pass
    return subprocess.run(["zstd", "-d", "-q", "-c"], input=blob,
                          stdout=subprocess.PIPE, check=True).stdout


# ------------------------------ frame layer ------------------------------ #
def aegis_frames(blob: bytes):
    off = 0
    while off + 16 <= len(blob):
        if blob[off:off+2] != b"\xa5\x3d":
            off += 1
            continue
        ln = struct.unpack(">H", blob[off+2:off+4])[0]
        total = 12 + ln + 4
        if off + total > len(blob):
            break
        frame = blob[off:off+total]
        ok = binascii.crc_hqx(frame[:-2], 0) == struct.unpack(">H", frame[-2:])[0]
        yield frame[12:12+ln], ok
        off += total


def parse_chunk(payload: bytes):
    """Decode one MLG1/MLG2 payload. Returns a dict or None."""
    if len(payload) < 6:
        return None
    magic, flags, nlen = payload[:4], payload[4], payload[5]

    if magic == MAGIC2:
        if len(payload) < 18 + nlen:
            return None
        gen, offset, total = struct.unpack(">III", payload[6:18])
        body = payload[18:]
    elif magic == MAGIC1:
        if len(payload) < 14 + nlen:
            return None
        gen, total = 0, None
        offset = struct.unpack(">Q", payload[6:14])[0]
        body = payload[14:]
    else:
        return None

    return {"name": body[:nlen].decode("utf-8", "replace"),
            "gen": gen, "offset": offset, "total": total, "data": body[nlen:],
            "complete": bool(flags & F_COMPLETE), "zstd": bool(flags & F_ZSTD)}


def pool_captures(paths):
    """Pool chunks from many captures, keyed by filename then generation.

    Returns (files, stats):
      files: name -> {gen -> {"offsets": {offset: data}, "complete": bool,
                              "conflicts": [...], "total": int|None,
                              "zstd": bool, "gen": int}}
      stats: str(path) -> {"frames": n, "bad_crc": n, "unknown": [hex, ...]}
    """
    files, stats = {}, {}
    for p in paths:
        blob = Path(p).read_bytes()
        st = {"frames": 0, "bad_crc": 0, "unknown": []}
        for payload, ok in aegis_frames(blob):
            st["frames"] += 1
            if not ok:
                st["bad_crc"] += 1
                continue
            c = parse_chunk(payload)
            if c is None:
                st["unknown"].append(payload[:32].hex())
                continue
            name = os.path.basename(c["name"])   # wire data: never allow paths
            ent = files.setdefault(name, {}).setdefault(
                c["gen"], {"offsets": {}, "complete": False, "conflicts": [],
                           "total": None, "zstd": False, "gen": c["gen"]})
            off = c["offset"]
            prev = ent["offsets"].get(off)
            if prev is not None and prev != c["data"] and off not in ent["conflicts"]:
                ent["conflicts"].append(off)
            ent["offsets"][off] = c["data"]      # later capture wins
            ent["complete"] |= c["complete"]
            ent["zstd"] |= c["zstd"]
            if c["total"] is not None:
                ent["total"] = c["total"]
        stats[str(p)] = st
    return files, stats


def assemble(offsets):
    """Concatenate chunks by offset. Returns (blob, gaps); holes are
    zero-filled so byte offsets in the output stay true to the source."""
    out, gaps, pos = bytearray(), [], 0
    for off in sorted(offsets):
        data = offsets[off]
        if off > pos:
            gaps.append((pos, off))
            out.extend(b"\x00" * (off - pos))
            pos = off
        if off < pos:                            # overlap: later data wins
            out[off:off+len(data)] = data
            pos = max(pos, off + len(data))
        else:
            out.extend(data)
            pos += len(data)
    return bytes(out), gaps


def decode_generation(ent):
    """Assemble and (if flagged) decompress one generation.

    Returns a report dict including "blob" (b"" when undecodable) and
    "intact" (no gaps, no conflicts, decoded cleanly)."""
    blob, gaps = assemble(ent["offsets"])
    if ent["total"] is not None and len(blob) < ent["total"]:
        gaps = gaps + [(len(blob), ent["total"])]      # missing tail chunk(s)
        blob += b"\x00" * (ent["total"] - len(blob))

    rep = {"gen": ent["gen"], "chunks": len(ent["offsets"]),
           "wire_bytes": len(blob), "wire_total": ent["total"],
           "zstd": ent["zstd"], "complete": ent["complete"],
           "gaps": [list(g) for g in gaps], "conflicts": ent["conflicts"],
           "error": None, "blob": b""}

    if not ent["zstd"]:
        rep["blob"] = blob                        # raw degrades gracefully
    elif gaps:
        rep["error"] = "compressed blob has gaps -- not decompressable"
    else:
        try:
            out = zstd_decompress(blob)
        except Exception as e:
            rep["error"] = f"zstd decompress failed: {type(e).__name__}: {e}"
        else:
            rep["blob"] = out
            if ent["gen"] and len(out) != ent["gen"]:
                rep["error"] = (f"decompressed {len(out)} B but gen says "
                                f"{ent['gen']} B")
    rep["bytes"] = len(rep["blob"])
    rep["intact"] = not (gaps or ent["conflicts"] or rep["error"])
    return rep


def choose(gens):
    """Best generation: prefer intact, then the largest source generation,
    then the most recovered bytes."""
    return max(gens, key=lambda g: (g["intact"], g["gen"], g["bytes"]))


# ----------------------------- CSV validation ----------------------------- #
def _scan_csv(blob: bytes):
    """Split into lines with byte positions and classify rows.

    Returns (header_line_or_None, valid, bad) where valid is a list of
    (byte_start, byte_end, ts_str, ts_dt, raw_line) and bad a count of
    non-empty data lines that are torn, hole-damaged, or malformed."""
    lines, pos = [], 0
    for raw in blob.split(b"\n"):
        lines.append((pos, raw))
        pos += len(raw) + 1
    if lines and lines[-1][1] == b"":
        lines.pop()                              # artifact of trailing newline

    header = None
    data_lines = lines
    if lines and lines[0][1].startswith(b"timestamp,"):
        header = lines[0][1]
        data_lines = lines[1:]

    expected = header.count(b",") if header is not None else None
    valid, bad = [], 0
    for start, raw in data_lines:
        if not raw:
            continue
        ts = None
        if b"\x00" not in raw and (expected is None or raw.count(b",") == expected):
            try:
                ts = datetime.fromisoformat(raw.split(b",", 1)[0].decode())
            except (ValueError, UnicodeDecodeError):
                ts = None
        if ts is None:
            bad += 1
            continue
        if expected is None:                     # headerless: first row sets shape
            expected = raw.count(b",")
        valid.append((start, start + len(raw), raw.split(b",", 1)[0].decode(),
                      ts, raw))
    return header, valid, bad


def validate_csv(blob: bytes, gaps=()):
    header, valid, bad = _scan_csv(blob)
    dts = [v[3] for v in valid]
    ctx = []
    for s, e in gaps:
        before = next((v[2] for v in reversed(valid) if v[1] <= s), None)
        after = next((v[2] for v in valid if v[0] >= e), None)
        ctx.append({"start": s, "end": e,
                    "last_row_before": before, "first_row_after": after})
    return {
        "header_ok": header is not None,
        "rows": len(valid),
        "bad_rows": bad,
        "monotonic": all(a <= b for a, b in zip(dts, dts[1:])),
        "first_ts": valid[0][2] if valid else None,
        "last_ts": valid[-1][2] if valid else None,
        "gap_context": ctx,
    }


def clean_csv(blob: bytes) -> bytes:
    """Header plus intact rows only -- hole-damaged and torn rows dropped."""
    header, valid, _ = _scan_csv(blob)
    out = [header] if header is not None else []
    out += [v[4] for v in valid]
    return b"\n".join(out) + b"\n" if out else b""


# ---------------------------------- CLI ---------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("captures", nargs="+")
    ap.add_argument("--outdir", required=True, type=Path)
    ap.add_argument("--clean", action="store_true",
                    help="also write <name>.clean.csv with intact rows only")
    ap.add_argument("--all-gens", action="store_true",
                    help="also write non-winning generations as <name>.gen<N>")
    ap.add_argument("--json", action="store_true",
                    help="print a machine-readable report instead of text")
    args = ap.parse_args(argv)

    files, stats = pool_captures(args.captures)
    args.outdir.mkdir(parents=True, exist_ok=True)

    report = {"captures": stats, "files": {}}
    all_ok = bool(files)

    for name, gens in sorted(files.items()):
        decoded = [decode_generation(e) for _, e in sorted(gens.items())]
        best = choose(decoded)

        for d in decoded:
            if d is not best and args.all_gens and d["blob"]:
                (args.outdir / f"{name}.gen{d['gen']}").write_bytes(d["blob"])
        (args.outdir / name).write_bytes(best["blob"])

        frep = {"generations": [{k: v for k, v in d.items() if k != "blob"}
                                for d in decoded],
                "chosen_gen": best["gen"], "bytes": best["bytes"],
                "chunks": best["chunks"], "complete": best["complete"],
                "gaps": best["gaps"], "conflicts": best["conflicts"],
                "zstd": best["zstd"], "error": best["error"]}
        if name.endswith(".csv") and best["blob"]:
            # gaps are wire-side; they only map onto output bytes when raw
            csv_gaps = [] if best["zstd"] else [tuple(g) for g in best["gaps"]]
            frep["csv"] = validate_csv(best["blob"], csv_gaps)
            if args.clean:
                (args.outdir / (name[:-4] + ".clean.csv")).write_bytes(
                    clean_csv(best["blob"]))
        report["files"][name] = frep
        if not (best["intact"] and best["complete"]):
            all_ok = False

    if args.json:
        print(json.dumps(report, indent=1))
        return 0 if all_ok else 1

    for src, st in stats.items():
        extra = "".join(f"\n      unrecognised payload: {h}..." for h in st["unknown"])
        print(f"  {Path(src).name}: {st['frames']} frame(s), "
              f"bad_crc={st['bad_crc']}, unknown={len(st['unknown'])}{extra}")
    print()
    if not files:
        print("no MELAGEN chunks found")
        return 1

    for name, frep in report["files"].items():
        state = "COMPLETE" if frep["complete"] else "in-progress"
        enc = "zstd" if frep["zstd"] else "raw"
        print(f"  {name}: {frep['bytes']:,} B from {frep['chunks']} chunk(s) "
              f"[{enc}, gen={frep['chosen_gen']}, {state}]")
        if len(frep["generations"]) > 1:
            others = ", ".join(
                f"gen={g['gen']}{'' if g['intact'] else ' BROKEN'}"
                for g in frep["generations"] if g["gen"] != frep["chosen_gen"])
            print(f"      other generations seen: {others}")
        if frep["error"]:
            print(f"      ERROR: {frep['error']}")
        if frep["gaps"]:
            print(f"      GAPS (wire offsets)={frep['gaps']}")
        if frep["conflicts"]:
            print(f"      CONFLICTING OFFSETS={frep['conflicts']}")
        csv_rep = frep.get("csv")
        if csv_rep:
            print(f"      csv: rows={csv_rep['rows']} bad={csv_rep['bad_rows']} "
                  f"header={'ok' if csv_rep['header_ok'] else 'MISSING'} "
                  f"monotonic={csv_rep['monotonic']} "
                  f"span={csv_rep['first_ts']} .. {csv_rep['last_ts']}")
            for g in csv_rep["gap_context"]:
                print(f"      gap {g['start']}..{g['end']}: last row before = "
                      f"{g['last_row_before']}, first row after = {g['first_row_after']}")
        print(f"      -> {args.outdir / name}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
