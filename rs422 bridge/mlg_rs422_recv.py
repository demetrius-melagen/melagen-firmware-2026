#!/usr/bin/env python3
# MELAGEN [AI-deployed, approved by Daniel 2026-08-18]
"""
MELAGEN RS-422 receiver -- inbound AEGIS frames + clock discipline.

Replaces Satlyt's incoming_daemon.py. Runs SYSTEM-scope (root) because
clock_settime needs CAP_SYS_TIME, which user units cannot hold.

What arrives on the link
------------------------
The flight computer (MSC-EC emulator on the bench) broadcasts a 1 Hz
heartbeat: a bare 16-byte AEGIS frame, message_id 0xffff, payload length 0.
Its only content is the 4-byte header timestamp. That timestamp is the
mission timebase and the sole reason this daemon exists.

Measured on Unit B: 149 frames in 150 s, 149/149 CRC valid.

Clock policy
------------
The flight computer is authoritative. The Nano is not. So:

    d = emulator_ts - system_time

    d >  +2 s            step forward
    |d| <= 2 s           deadband -- heartbeat resolution IS 1 s, so anything
                         smaller is below the reference's own noise floor
    -300 s <= d < -2 s   step back (small correction, e.g. after the operator
                         manually syncs a stale emulator)
    d < -300 s           REFUSE. Mark UNTRUSTED. This is the "emulator has not
                         been synced yet" case -- on the bench it has been seen
                         3.5 days behind. Never follow it.

Before the first step after boot we require N_LOCK consecutive heartbeats
whose timestamps advance in step with CLOCK_MONOTONIC, so one stale or
replayed frame cannot move the clock.

A backward step can strand up to `d` seconds of rows in the previous hourly
file (the sender picks the newest bucket by name, so a reopened older bucket
is not re-sent). That is bounded by the 300 s cap and accepted deliberately.

Boot-time restore is NOT done here. systemd-timesyncd already bumps the clock
forward to the mtime of /var/lib/systemd/timesync/clock at startup, forward-only.
Satlyt hand-rolled that and persisted the wrong value; we use systemd's.

Outputs
-------
  /media/Data/Melagen/clock_ref.json   current offset + lock state (atomic)
  /media/Data/Melagen/logs/rs422_recv.log   rotated at 8 MB, keeps 1

Non-heartbeat frames are written to SPOOL_DIR as the future commanding hook.

Usage:
  mlg_rs422_recv.py                 # discipline the clock (what the unit runs)
  mlg_rs422_recv.py --observe       # never touch the clock, just record
  mlg_rs422_recv.py --observe --duration 60   # bounded run, for testing
"""

import argparse
import binascii
import json
import os
import struct
import subprocess
import sys
import time

PORT        = "/dev/ttyTHS1"
BAUD        = 115200
SOM         = b"\xa5\x3d"
HEARTBEAT   = 0xFFFF

STATE_FILE  = "/media/Data/Melagen/clock_ref.json"
SPOOL_DIR   = "/media/Data/Melagen/incoming"
LOGFILE     = "/var/log/melagen/rs422_recv.log"   # root fs: survives a degraded data volume
LOG_MAX     = 8 * 1024 * 1024

DEADBAND      = 2.0       # s -- heartbeat timestamps are integer seconds
MAX_STEP_BACK = 300.0     # s -- refuse anything further behind than this
MAX_STEP_FWD  = 86400.0 * 3650   # sanity ceiling (~10 yr) on forward jumps
N_LOCK        = 3         # consecutive consistent beats before the first step
LOCK_TOL      = 2.0       # s -- allowed |emu delta - monotonic delta| while locking
HOLDOVER_S    = 30.0      # s of silence before we stop trusting the reference
STATE_EVERY   = 5.0       # s between clock_ref.json writes
NOTICE_EVERY  = 300.0     # s -- repeat a standing verdict at most this often
PERSIST_EVERY = 300.0     # s between boot-floor refreshes while LOCKED

# systemd-timesyncd bumps the clock forward to this file's mtime at startup.
# With NTP disabled systemd never rewrites it, so WE maintain it -- that is the
# "fake-hwclock" floor that keeps a cold boot out of 1969. We write the real
# post-sync time (Satlyt's bug was persisting a stale pre-update local value).
TSYNC_CLOCK   = "/var/lib/systemd/timesync/clock"


# ---------------- logging ---------------- #
def log(msg: str, err: bool = False) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}",
          file=sys.stderr if err else sys.stdout, flush=True)


def rotate_log() -> None:
    """logrotate is not installed on this unit; bound the file ourselves."""
    try:
        if os.path.getsize(LOGFILE) >= LOG_MAX:
            os.replace(LOGFILE, LOGFILE + ".1")
    except OSError:
        pass


# ---------------- clock policy (pure, unit-tested) ---------------- #
def decide(delta: float, locked: bool):
    """Given d = emulator - system, return (action, reason).

    action: 'none' | 'forward' | 'back' | 'refuse' | 'wait_lock'
    """
    if abs(delta) <= DEADBAND:
        return "none", f"within deadband ({delta:+.1f}s)"
    if delta < -MAX_STEP_BACK:
        return "refuse", (f"reference is {-delta:.0f}s behind us "
                          f"(> {MAX_STEP_BACK:.0f}s cap) -- not synced yet")
    if delta > MAX_STEP_FWD:
        return "refuse", f"implausible forward jump ({delta:+.0f}s)"
    if not locked:
        return "wait_lock", f"holding {delta:+.1f}s until reference locks"
    return ("forward" if delta > 0 else "back"), f"{delta:+.1f}s"


class Notifier:
    """Log a standing verdict on change, then at most every NOTICE_EVERY.

    Without this, a refusal or a settling hold is re-emitted on every heartbeat
    -- 86,400 lines/day at 1 Hz, which would rotate the log daily and bury
    everything worth reading."""

    def __init__(self):
        self.action = None
        self.last = 0.0

    def notice(self, action: str, msg: str, mono: float, err: bool = False) -> None:
        if action != self.action or mono - self.last >= NOTICE_EVERY:
            self.action = action
            self.last = mono
            log(msg, err=err)


def persist_time() -> None:
    """Refresh the boot-time floor: timesyncd's clock file + the hardware RTC."""
    now = time.time()
    try:
        os.makedirs(os.path.dirname(TSYNC_CLOCK), exist_ok=True)
        if not os.path.exists(TSYNC_CLOCK):
            open(TSYNC_CLOCK, "a").close()
        os.utime(TSYNC_CLOCK, (now, now))
    except OSError as e:
        log(f"could not refresh {TSYNC_CLOCK}: {e}", err=True)
    if os.path.exists("/dev/rtc0"):
        try:
            subprocess.run(["hwclock", "--systohc"], check=True,
                           capture_output=True, timeout=15)
        except Exception as e:
            log(f"hwclock --systohc failed: {type(e).__name__}: {e}", err=True)


def can_set_clock() -> bool:
    """Probe CAP_SYS_TIME without logging; a no-op set of the current time."""
    try:
        time.clock_settime(time.CLOCK_REALTIME, time.time())
        return True
    except Exception:
        return False


def set_clock(target: float) -> bool:
    try:
        time.clock_settime(time.CLOCK_REALTIME, target)
        return True
    except PermissionError:
        log("cannot set clock: needs CAP_SYS_TIME (run system-scope)", err=True)
    except (AttributeError, OSError) as e:
        log(f"cannot set clock: {type(e).__name__}: {e}", err=True)
    return False


# ---------------- frame layer ---------------- #
def iter_frames(buf: bytearray):
    """Yield (timestamp, device_id, message_id, payload, crc_ok), consuming buf."""
    while True:
        i = buf.find(SOM)
        if i < 0:
            del buf[:max(0, len(buf) - 1)]      # keep a possible split SOM byte
            return
        if len(buf) - i < 16:
            del buf[:i]
            return
        ln = struct.unpack(">H", buf[i+2:i+4])[0]
        total = 12 + ln + 4
        if len(buf) - i < total:
            del buf[:i]
            return
        frame = bytes(buf[i:i+total])
        del buf[:i+total]
        ok = binascii.crc_hqx(frame[:-2], 0) == struct.unpack(">H", frame[-2:])[0]
        ts, dev, mid = struct.unpack(">IHH", frame[4:12])
        yield ts, dev, mid, frame[12:12+ln], ok


# ---------------- state file ---------------- #
def write_state(st: dict) -> None:
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(st, fh, indent=1)
        os.replace(tmp, STATE_FILE)             # atomic for concurrent readers
    except OSError as e:
        log(f"could not write {STATE_FILE}: {e}", err=True)


# ---------------- main loop ---------------- #
def run(observe: bool = False, duration: float = 0.0, port: str = PORT) -> int:
    import serial

    rotate_log()
    log(f"starting on {port} @ {BAUD}  mode={'observe' if observe else 'discipline'}")

    buf = bytearray()
    lock_run = 0                 # consecutive consistent beats
    locked = False
    prev = None                  # (emu_ts, monotonic) of the previous beat
    last_state_write = 0.0
    last_persist = 0.0
    last_beat = None
    notifier = Notifier()
    counts = {"frames": 0, "bad_crc": 0, "hb": 0, "other": 0, "steps": 0}
    t_start = time.monotonic()

    ser = None
    for attempt in range(1, 6):
        try:
            ser = serial.Serial(port=port, baudrate=BAUD, timeout=1)
            break
        except Exception as e:
            log(f"open failed ({e}); retry {attempt}/5", err=True)
            time.sleep(2)
    if ser is None:
        log("could not open serial port; exiting", err=True)
        return 1

    try:
        while True:
            if duration and time.monotonic() - t_start >= duration:
                break
            try:
                chunk = ser.read(512)
            except Exception as e:
                log(f"serial read error: {e}", err=True)
                time.sleep(1)
                continue

            mono = time.monotonic()
            if chunk:
                buf += chunk
                for ts, dev, mid, payload, ok in iter_frames(buf):
                    counts["frames"] += 1
                    if not ok:
                        counts["bad_crc"] += 1
                        continue
                    if mid != HEARTBEAT:
                        counts["other"] += 1
                        _spool(mid, dev, ts, payload)
                        continue

                    counts["hb"] += 1
                    last_beat = mono
                    if ts <= 0:
                        continue

                    # --- lock tracking: does the reference advance like a clock? ---
                    if prev is not None:
                        d_emu = ts - prev[0]
                        d_mono = mono - prev[1]
                        if abs(d_emu - d_mono) <= LOCK_TOL and d_emu >= 0:
                            lock_run += 1
                        else:
                            if locked or lock_run:
                                log(f"reference jumped {d_emu - d_mono:+.1f}s vs "
                                    f"monotonic; relocking")
                            lock_run = 0
                            locked = False
                    prev = (ts, mono)
                    if not locked and lock_run >= N_LOCK:
                        locked = True
                        log(f"reference LOCKED after {lock_run} consistent beats")

                    # --- policy ---
                    delta = ts - time.time()
                    action, reason = decide(delta, locked)
                    if action in ("forward", "back"):
                        if observe:
                            # standing verdict: in observe mode nothing ever
                            # corrects it, so this must not fire every beat
                            notifier.notice(f"observe_{action}",
                                            f"[observe] would step {action}: {reason}",
                                            mono)
                        else:
                            before = time.time()
                            if not can_set_clock():
                                notifier.notice("no_cap",
                                    "cannot set clock: needs CAP_SYS_TIME "
                                    "(must run system-scope)", mono, err=True)
                            elif set_clock(float(ts)):
                                counts["steps"] += 1
                                log(f"clock stepped {action} {reason} "
                                    f"({time.strftime('%F %T', time.localtime(before))}"
                                    f" -> {time.strftime('%F %T', time.localtime(ts))})")
                                prev = (ts, mono)   # our own step is not a jump
                                notifier.action = None  # a real step clears the verdict
                                persist_time()
                                last_persist = mono
                    elif action == "refuse":
                        notifier.notice("refuse", f"REFUSED: {reason}", mono, err=True)
                    elif action == "wait_lock":
                        notifier.notice("wait_lock", f"not stepping: {reason}", mono)
                    else:
                        notifier.notice("none", f"clock in sync ({reason})", mono)

            # --- keep the cold-boot floor fresh while we trust the reference ---
            if (locked and not observe
                    and time.monotonic() - last_persist >= PERSIST_EVERY):
                last_persist = time.monotonic()
                persist_time()

            # --- periodic state publication ---
            now_m = time.monotonic()
            if now_m - last_state_write >= STATE_EVERY:
                last_state_write = now_m
                silent = (now_m - last_beat) if last_beat else None
                if last_beat is None:
                    state = "NO_SIGNAL"
                elif silent > HOLDOVER_S:
                    state = "HOLDOVER"
                elif locked:
                    state = "LOCKED"
                else:
                    state = "SETTLING"
                write_state({
                    "state": state,
                    "emu_ts": prev[0] if prev else None,
                    "local_ts": time.time(),
                    "offset": (prev[0] - time.time()) if prev else None,
                    "silent_s": round(silent, 1) if silent is not None else None,
                    "locked": locked,
                    "observe": observe,
                    "counts": dict(counts),
                    "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
    except KeyboardInterrupt:
        log("interrupted")
    finally:
        ser.close()
        log(f"stopped: {counts}")
    return 0


def _spool(mid: int, dev: int, ts: int, payload: bytes) -> None:
    """Park non-heartbeat frames for a future commanding path."""
    try:
        os.makedirs(SPOOL_DIR, exist_ok=True)
        name = f"{ts}_{dev:04x}_{mid:04x}.bin"
        with open(os.path.join(SPOOL_DIR, name), "wb") as fh:
            fh.write(payload)
        log(f"spooled non-heartbeat msg=0x{mid:04x} dev={dev} len={len(payload)}")
    except OSError as e:
        log(f"could not spool frame: {e}", err=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--observe", action="store_true",
                    help="never set the clock; log what would happen")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop after N seconds (0 = run forever)")
    ap.add_argument("--port", default=PORT)
    args = ap.parse_args()
    try:
        return run(observe=args.observe, duration=args.duration, port=args.port)
    except Exception as e:
        log(f"FATAL: {type(e).__name__}: {e}", err=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
