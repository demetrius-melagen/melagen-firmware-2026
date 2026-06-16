#!/usr/bin/env python3

import time
from smbus2 import SMBus, i2c_msg
import csv
import os
from datetime import datetime

# ==========================================================
# Logging Configuration
# ==========================================================
PRIMARY_LOG_DIR = "radfet_logs"

# Example backup locations:
# Linux external drive:
# BACKUP_LOG_DIR = "/media/usr/####-####"

BACKUP_LOG_DIR = "backup_logs"

# Rotation intervals
DAY = 86400
HOUR = 3600
MINUTE = 60

# Change this as needed
LOG_INTERVAL_SECONDS = DAY

# ==========================================================
# Dosimeter Tuning Parameters
# ==========================================================
A = 0.02951
B = 0.45509

# Dosimeter Unique Baseline Voltages
#calibrated values from lead brick analysis
REF_V = [[1.7+0.254783568,1.7+0.273382173],
        [1.7+0.251159951,1.7+0.278103378],
        [1.7+0.293569394,1.7+0.279690680],
        [1.7+0.168823769,1.7+0.257753358],
        [1.7+0.387912088,1.7+0.380667481]] 

# ==========================================================
# I2C Bus Definitions
# ==========================================================
ADS_BUS = 1
TCA_BUS = 7

# ==========================================================
# Device Addresses
# ==========================================================
TCA9539_ADDR = 0x74
ADS_ADDR = 0x10
VREF = 5.0

# ==========================================================
# ADS7138 Opcodes
# ==========================================================
ADS_OPCODE_READ  = 0x10
ADS_OPCODE_WRITE = 0x08

# ==========================================================
# TCA9539 Registers
# ==========================================================
REG_OUTPUT_PORT0 = 0x02
REG_OUTPUT_PORT1 = 0x03
REG_CONFIG_PORT0 = 0x06
REG_CONFIG_PORT1 = 0x07

# ==========================================================
# ADS7138 Registers
# ==========================================================
REG_GENERAL_CFG  = 0x01
REG_DATA_CFG     = 0x02
REG_OPMODE_CFG   = 0x04
REG_PIN_CFG      = 0x05
REG_SEQUENCE_CFG = 0x10
REG_CHANNEL_SEL  = 0x11

# ==========================================================
# Bit Definitions
# ==========================================================
P00_FET1_CTL = 1 << 0
P01_FET1_R1  = 1 << 1
P02_FET1_R2  = 1 << 2
P03_FET2_CTL = 1 << 3
P04_FET2_R1  = 1 << 4
P05_FET2_R2  = 1 << 5
P06_FET3_CTL = 1 << 6
P07_FET3_R1  = 1 << 7

P10_FET3_R2  = 1 << 0
P11_FET4_CTL = 1 << 1
P12_FET4_R1  = 1 << 2
P13_FET4_R2  = 1 << 3
P14_FET5_CTL = 1 << 4
P15_FET5_R1  = 1 << 5
P16_FET5_R2  = 1 << 6

R1_PORT0 = (
    P00_FET1_CTL |
    P01_FET1_R1  |
    P03_FET2_CTL |
    P04_FET2_R1  |
    P06_FET3_CTL |
    P07_FET3_R1
)

R1_PORT1 = (
    P11_FET4_CTL |
    P12_FET4_R1  |
    P14_FET5_CTL |
    P15_FET5_R1
)

R2_PORT0 = (
    P00_FET1_CTL |
    P02_FET1_R2  |
    P03_FET2_CTL |
    P05_FET2_R2  |
    P06_FET3_CTL
)

R2_PORT1 = (
    P10_FET3_R2  |
    P11_FET4_CTL |
    P13_FET4_R2  |
    P14_FET5_CTL |
    P16_FET5_R2
)

# ==========================================================
# Time Bucket Functions
# ==========================================================
def get_time_bucket():

    now = datetime.now()

    if LOG_INTERVAL_SECONDS >= DAY:

        return now.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

    elif LOG_INTERVAL_SECONDS >= HOUR:

        return now.replace(
            minute=0,
            second=0,
            microsecond=0
        )

    elif LOG_INTERVAL_SECONDS >= MINUTE:

        bucket_minutes = (
            now.minute //
            (LOG_INTERVAL_SECONDS // 60)
        ) * (LOG_INTERVAL_SECONDS // 60)

        return now.replace(
            minute=bucket_minutes,
            second=0,
            microsecond=0
        )

    else:

        bucket_seconds = (
            now.second //
            LOG_INTERVAL_SECONDS
        ) * LOG_INTERVAL_SECONDS

        return now.replace(
            second=bucket_seconds,
            microsecond=0
        )

# ==========================================================
# CSV File Management
# ==========================================================
def build_filename(base_dir):

    try:

        os.makedirs(base_dir, exist_ok=True)

    except Exception as e:

        print(
            f"\n[LOG ERROR] Cannot access directory: {base_dir}"
        )

        print(
            f"{type(e).__name__}: {e}"
        )

        return None

    bucket_time = get_time_bucket()

    if LOG_INTERVAL_SECONDS >= DAY:
        fmt = "%Y-%m-%d"

    elif LOG_INTERVAL_SECONDS >= HOUR:
        fmt = "%Y-%m-%d_%H"

    elif LOG_INTERVAL_SECONDS >= MINUTE:
        fmt = "%Y-%m-%d_%H-%M"

    else:
        fmt = "%Y-%m-%d_%H-%M-%S"

    time_str = bucket_time.strftime(fmt)

    return os.path.join(
        base_dir,
        f"radfet_{time_str}.csv"
    )

def open_log_file(filename):

    file_exists = os.path.isfile(filename)

    f = open(filename, "a", newline="")
    writer = csv.writer(f)

    if not file_exists:

        writer.writerow([
            "timestamp",
            "sensor_group",
            "channel",
            "raw_adc",
            "voltage",
            "dose_rad"
        ])

    return {
        "file": f,
        "writer": writer,
        "name": filename
    }

def initialize_loggers():

    return {
        "primary": {
            "file": None,
            "writer": None,
            "name": None
        },

        "backup": {
            "file": None,
            "writer": None,
            "name": None
        }
    }

def try_open_log_file(filename):

    try:

        return open_log_file(filename)

    except Exception as e:

        print(
            f"\n[LOG ERROR] Cannot open {filename}"
        )

        print(
            f"{type(e).__name__}: {e}"
        )

        return {
            "file": None,
            "writer": None,
            "name": filename
        }

def safe_write_logger(logger, row, logger_name):

    try:

        if (
            logger["file"] is None or
            logger["writer"] is None
        ):
            return False

        logger["writer"].writerow(row)
        logger["file"].flush()

        return True

    except Exception as e:

        print(
            f"\n[LOG ERROR] {logger_name} write failed: "
            f"{type(e).__name__}: {e}"
        )

        try:
            logger["file"].close()
        except:
            pass

        logger["file"] = None
        logger["writer"] = None

        return False

def update_log_files(loggers):

    # --------------------------------------------------
    # Primary location
    # --------------------------------------------------
    try:

        primary_filename = build_filename(
            PRIMARY_LOG_DIR
        )

    except Exception as e:

        print(
            f"\n[LOG ERROR] Primary log unavailable"
        )

        print(
            f"{type(e).__name__}: {e}"
        )

        primary_filename = None

    # --------------------------------------------------
    # Backup location
    # --------------------------------------------------
    try:

        backup_filename = build_filename(
            BACKUP_LOG_DIR
        )

    except Exception as e:

        print(
            f"\n[LOG ERROR] Backup log unavailable"
        )

        print(
            f"{type(e).__name__}: {e}"
        )

        backup_filename = None

    # --------------------------------------------------
    # Open / rotate primary file
    # --------------------------------------------------
    if (
        primary_filename is not None and
        (
            loggers["primary"]["name"] != primary_filename or
            loggers["primary"]["file"] is None
        )
    ):

        try:

            if loggers["primary"]["file"]:

                loggers["primary"]["file"].close()

        except:
            pass

        loggers["primary"] = try_open_log_file(
            primary_filename
        )

    # --------------------------------------------------
    # Open / rotate backup file
    # --------------------------------------------------
    if (
        backup_filename is not None and
        (
            loggers["backup"]["name"] != backup_filename or
            loggers["backup"]["file"] is None
        )
    ):

        try:

            if loggers["backup"]["file"]:

                loggers["backup"]["file"].close()

        except:
            pass

        loggers["backup"] = try_open_log_file(
            backup_filename
        )

    # --------------------------------------------------
    # Mark unavailable destinations offline
    # --------------------------------------------------
    if primary_filename is None:

        loggers["primary"]["file"] = None
        loggers["primary"]["writer"] = None

    if backup_filename is None:

        loggers["backup"]["file"] = None
        loggers["backup"]["writer"] = None
def write_log_row(loggers, row):

    update_log_files(loggers)

    primary_ok = safe_write_logger(
        loggers["primary"],
        row,
        "PRIMARY"
    )

    backup_ok = safe_write_logger(
        loggers["backup"],
        row,
        "BACKUP"
    )

    if not primary_ok and not backup_ok:

        print(
            "\n[CRITICAL] Unable to write "
            "to primary or backup logging destination"
        )

# ==========================================================
# ADS Functions
# ==========================================================
def ads_write_reg(bus, reg, val):

    try:

        msg = i2c_msg.write(
            ADS_ADDR,
            [ADS_OPCODE_WRITE, reg, val]
        )

        bus.i2c_rdwr(msg)

        return True

    except Exception as e:

        print(
            f"\n[I2C ERROR] ADS WRITE FAILED | "
            f"ADDR=0x{ADS_ADDR:02X} "
            f"REG=0x{reg:02X} "
            f"VAL=0x{val:02X}"
        )

        print(f"Exception: {type(e).__name__}: {e}")

        return False

def ads_read_reg(bus, reg):

    try:

        cmd = i2c_msg.write(
            ADS_ADDR,
            [ADS_OPCODE_READ, reg]
        )

        read = i2c_msg.read(ADS_ADDR, 1)

        bus.i2c_rdwr(cmd, read)

        return list(read)[0]

    except Exception as e:

        print(
            f"\n[I2C ERROR] ADS READ FAILED | "
            f"ADDR=0x{ADS_ADDR:02X} "
            f"REG=0x{reg:02X}"
        )

        print(f"Exception: {type(e).__name__}: {e}")

        return None

# baseline_voltage = 1.7 #use constant for testing, update code to assign unique base for each sensor
def ads_read_adc(bus, baseline_voltage):
    try:
        read = i2c_msg.read(ADS_ADDR, 2)
        bus.i2c_rdwr(read)
        data = list(read)
        raw = ((data[0] << 8) | data[1]) >> 4
        voltage = raw * VREF / 4095.0
        delta_v = voltage - baseline_voltage
        if delta_v <= 0:
            dose = 0
        else:
            dose = (delta_v / A) ** (1.0 / B)
        return raw, delta_v, dose
    except Exception as e:
        print("ADC read error:", e)
        return None, None, None

# ==========================================================
# TCA Control
# ==========================================================
def tca_write(bus, reg, val):

    try:

        bus.write_byte_data(
            TCA9539_ADDR,
            reg,
            val
        )

        return True

    except Exception as e:

        print(
            f"\n[I2C ERROR] TCA WRITE FAILED | "
            f"ADDR=0x{TCA9539_ADDR:02X} "
            f"REG=0x{reg:02X} "
            f"VAL=0x{val:02X}"
        )

        print(f"Exception: {type(e).__name__}: {e}")

        return False

def update_io_expander(bus, p0, p1):

    success0 = tca_write(bus, REG_OUTPUT_PORT0, p0)
    success1 = tca_write(bus, REG_OUTPUT_PORT1, p1)

    if not (success0 and success1):

        print(
            "\n[ERROR] Failed to update IO expander "
            f"(P0=0x{p0:02X}, P1=0x{p1:02X})"
        )

    return success0 and success1

def tca9539_config(bus):

    print("\nConfiguring TCA9539 IO expander...")

    success = (
        tca_write(bus, REG_OUTPUT_PORT0, 0x00) and
        tca_write(bus, REG_OUTPUT_PORT1, 0x00) and
        tca_write(bus, REG_CONFIG_PORT0, 0x00) and
        tca_write(bus, REG_CONFIG_PORT1, 0x00)
    )

    if success:
        print("TCA9539 configuration successful")
    else:
        print("TCA9539 configuration FAILED")

    return success

def enable_r1(bus):

    print("\nEnabling R1 sensor bank...")

    return update_io_expander(
        bus,
        R1_PORT0,
        R1_PORT1
    )

def enable_r2(bus):

    print("\nEnabling R2 sensor bank...")

    return update_io_expander(
        bus,
        R2_PORT0,
        R2_PORT1
    )

def disable_all(bus):

    print("\nDisabling all sensors...")

    return update_io_expander(
        bus,
        0x00,
        0x00
    )

# ==========================================================
# ADC Read + Log
# ==========================================================
def read_all_channels(bus, loggers, group):

    print(f"\nReading sensor group: {group}")

    for ch in range(2, 7):

        print(f"\nSelecting ADS channel {ch}")

        if not ads_write_reg(bus, REG_CHANNEL_SEL, ch):

            print(f"[ERROR] Failed to select channel {ch}")

            continue

        verify = ads_read_reg(bus, REG_CHANNEL_SEL)

        if verify != ch:

            print(
                f"[VERIFY ERROR] "
                f"Wrote CH{ch} but read back {verify}"
            )

            continue

        raw, voltage, dose = ads_read_adc(bus,REF_V[ch-2][0 if group == "R1" else 1])

        if raw is None:

            print(f"[ERROR] ADC read failed on CH{ch}")

            continue

        print(
            f"CH{ch - 1} | "
            f"RAW={raw} | "
            f"V={voltage:.6f} V | "
            f"DOSE={dose:.6f} rad"
        )

        row = [
            datetime.now().isoformat(),
            group,
            ch - 1,
            raw,
            voltage,
            dose
        ]

        write_log_row(loggers, row)

        time.sleep(0.01)

# ==========================================================
# Main
# ==========================================================
print("\n========================================")
print("RADFET DOSIMETER LOGGER STARTING")
print("========================================")

loggers = initialize_loggers()

try:

    with SMBus(ADS_BUS) as ads_bus, SMBus(TCA_BUS) as tca_bus:

        print("\nOpened I2C buses successfully")

        if not tca9539_config(tca_bus):

            print("\n[FATAL ERROR] Failed to configure TCA9539")
            exit(1)

        time.sleep(0.05)

        # ---------------- R1 ----------------
        if enable_r1(tca_bus):

            time.sleep(0.2)

            read_all_channels(
                ads_bus,
                loggers,
                "R1"
            )

        disable_all(tca_bus)

        time.sleep(0.5)

        # ---------------- R2 ----------------
        if enable_r2(tca_bus):

            time.sleep(0.2)

            read_all_channels(
                ads_bus,
                loggers,
                "R2"
            )

        disable_all(tca_bus)

except Exception as e:

    print("\n[FATAL ERROR]")
    print(f"{type(e).__name__}: {e}")

# ==========================================================
# Cleanup
# ==========================================================
print("\nCleaning up log files...")

for logger in loggers.values():

    if logger["file"]:

        logger["file"].close()

        print(
            f"Closed: "
            f"{os.path.abspath(logger['name'])}"
        )

print("\n========================================")
print("DONE")
print("========================================")

print("\nLogs stored in:")

print(
    f"  Primary: "
    f"{os.path.abspath(PRIMARY_LOG_DIR)}"
)

print(
    f"  Backup : "
    f"{os.path.abspath(BACKUP_LOG_DIR)}"
)

print("")