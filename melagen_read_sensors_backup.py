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
# BACKUP_LOG_DIR = "/media/pi/USB_DRIVE/radfet_backup"

# Windows:
# BACKUP_LOG_DIR = "D:/radfet_backup"

# Raspberry Pi / Linux example:
BACKUP_LOG_DIR = "Downloads"

# Rotation intervals
DAY = 86400
HOUR = 3600
MINUTE = 60

# Change this as needed
LOG_INTERVAL_SECONDS = DAY

# ==========================================================
# Dosimeter Calibration Variables
# ==========================================================
A = 0.02951
B = 0.45509

# ==========================================================
# I2C Bus Definitions
# ==========================================================
ADS_BUS = 1
TCA_BUS = 1

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

    os.makedirs(base_dir, exist_ok=True)

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

def update_log_files(loggers):

    primary_filename = build_filename(PRIMARY_LOG_DIR)
    backup_filename  = build_filename(BACKUP_LOG_DIR)

    # ---------------- PRIMARY ----------------
    if loggers["primary"]["name"] != primary_filename:

        if loggers["primary"]["file"]:
            loggers["primary"]["file"].close()

        loggers["primary"] = open_log_file(primary_filename)

        print("\nPrimary log file:")
        print(os.path.abspath(primary_filename))

    # ---------------- BACKUP ----------------
    if loggers["backup"]["name"] != backup_filename:

        if loggers["backup"]["file"]:
            loggers["backup"]["file"].close()

        loggers["backup"] = open_log_file(backup_filename)

        print("\nBackup log file:")
        print(os.path.abspath(backup_filename))

def write_log_row(loggers, row):

    update_log_files(loggers)

    # Write to primary
    loggers["primary"]["writer"].writerow(row)
    loggers["primary"]["file"].flush()

    # Write to backup
    loggers["backup"]["writer"].writerow(row)
    loggers["backup"]["file"].flush()

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

    except:
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

    except:
        return None

def ads_read_adc(bus):

    try:
        read = i2c_msg.read(ADS_ADDR, 2)

        bus.i2c_rdwr(read)

        data = list(read)

        raw = ((data[0] << 8) | data[1]) >> 4

        voltage = raw * VREF / 4095.0

        dose = (voltage / A) ** (1.0 / B)

        return raw, voltage, dose

    except:
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

    except:
        return False

def update_io_expander(bus, p0, p1):

    return (
        tca_write(bus, REG_OUTPUT_PORT0, p0) &
        tca_write(bus, REG_OUTPUT_PORT1, p1)
    )

def tca9539_config(bus):

    return (
        tca_write(bus, REG_OUTPUT_PORT0, 0x00) &
        tca_write(bus, REG_OUTPUT_PORT1, 0x00) &
        tca_write(bus, REG_CONFIG_PORT0, 0x00) &
        tca_write(bus, REG_CONFIG_PORT1, 0x00)
    )

def enable_r1(bus):

    return update_io_expander(
        bus,
        R1_PORT0,
        R1_PORT1
    )

def enable_r2(bus):

    return update_io_expander(
        bus,
        R2_PORT0,
        R2_PORT1
    )

def disable_all(bus):

    return update_io_expander(
        bus,
        0x00,
        0x00
    )

# ==========================================================
# ADC Read + Log
# ==========================================================
def read_all_channels(bus, loggers, group):

    for ch in range(2, 7):

        ads_write_reg(bus, REG_CHANNEL_SEL, ch)

        if ads_read_reg(bus, REG_CHANNEL_SEL) != ch:
            continue

        raw, voltage, dose = ads_read_adc(bus)

        if raw is None:
            continue

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
loggers = initialize_loggers()

with SMBus(ADS_BUS) as ads_bus, SMBus(TCA_BUS) as tca_bus:

    tca9539_config(tca_bus)

    time.sleep(0.05)

    # ---------------- R1 ----------------
    enable_r1(tca_bus)

    time.sleep(0.2)

    read_all_channels(
        ads_bus,
        loggers,
        "R1"
    )

    disable_all(tca_bus)

    time.sleep(0.5)

    # ---------------- R2 ----------------
    enable_r2(tca_bus)

    time.sleep(0.2)

    read_all_channels(
        ads_bus,
        loggers,
        "R2"
    )

    disable_all(tca_bus)

# ==========================================================
# Cleanup
# ==========================================================
for logger in loggers.values():

    if logger["file"]:
        logger["file"].close()

print("\nDone. Logs stored in:")
print(f"  Primary: {os.path.abspath(PRIMARY_LOG_DIR)}")
print(f"  Backup : {os.path.abspath(BACKUP_LOG_DIR)}\n")