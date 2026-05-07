#!/usr/bin/env python3
import time
from smbus2 import SMBus, i2c_msg
import csv
import os
from datetime import datetime

# ==========================================================
# Logging Configuration
# ==========================================================
LOG_DIR = "radfet_logs"

# Change this to control file rotation interval
# 86400 = daily, 3600 = hourly, 60 = minutely, etc.
DAY = 86400
HOUR = 3600
MINUTE = 60 
LOG_INTERVAL_SECONDS = DAY

def get_time_bucket():
    now = datetime.now()

    if LOG_INTERVAL_SECONDS >= DAY:
        # Start of local day
        return now.replace(hour=0, minute=0, second=0, microsecond=0)

    elif LOG_INTERVAL_SECONDS >= HOUR:
        # Start of local hour
        return now.replace(minute=0, second=0, microsecond=0)

    elif LOG_INTERVAL_SECONDS >= MINUTE:
        # Start of local minute bucket
        minutes = (now.minute // (LOG_INTERVAL_SECONDS // 60)) * (LOG_INTERVAL_SECONDS // 60)
        return now.replace(minute=minutes, second=0, microsecond=0)

    else:
        # Sub-minute buckets
        seconds = (now.second // LOG_INTERVAL_SECONDS) * LOG_INTERVAL_SECONDS
        return now.replace(second=seconds, microsecond=0)
def get_csv_filename():
    os.makedirs(LOG_DIR, exist_ok=True)
    bucket_time = get_time_bucket()

    # Adaptive formatting based on interval
    if LOG_INTERVAL_SECONDS >= DAY:
        fmt = "%Y-%m-%d"
    elif LOG_INTERVAL_SECONDS >= HOUR:
        fmt = "%Y-%m-%d_%H"
    elif LOG_INTERVAL_SECONDS >= MINUTE:
        fmt = "%Y-%m-%d_%H-%M"
    else:
        fmt = "%Y-%m-%d_%H-%M-%S"

    time_str = bucket_time.strftime(fmt)
    return os.path.join(LOG_DIR, f"radfet_{time_str}.csv")

def get_csv_writer(current_file):
    filename = get_csv_filename()

    if current_file["name"] != filename:
        if current_file["file"]:
            current_file["file"].close()

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

        current_file["file"] = f
        current_file["writer"] = writer
        current_file["name"] = filename

        print(f"\nSwitched to new file: {os.path.abspath(filename)}")

    return current_file["writer"]

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

R1_PORT0 = P00_FET1_CTL | P01_FET1_R1 | P03_FET2_CTL | P04_FET2_R1 | P06_FET3_CTL | P07_FET3_R1
R1_PORT1 = P11_FET4_CTL | P12_FET4_R1 | P14_FET5_CTL | P15_FET5_R1

R2_PORT0 = P00_FET1_CTL | P02_FET1_R2 | P03_FET2_CTL | P05_FET2_R2 | P06_FET3_CTL
R2_PORT1 = P10_FET3_R2 | P11_FET4_CTL | P13_FET4_R2 | P14_FET5_CTL | P16_FET5_R2

# ==========================================================
# ADS Functions
# ==========================================================
def ads_write_reg(bus, reg, val):
    try:
        msg = i2c_msg.write(ADS_ADDR, [ADS_OPCODE_WRITE, reg, val])
        bus.i2c_rdwr(msg)
        return True
    except:
        return False

def ads_read_reg(bus, reg):
    try:
        cmd = i2c_msg.write(ADS_ADDR, [ADS_OPCODE_READ, reg])
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
        bus.write_byte_data(TCA9539_ADDR, reg, val)
        return True
    except:
        return False

def update_io_expander(bus, p0, p1):
    return tca_write(bus, REG_OUTPUT_PORT0, p0) & tca_write(bus, REG_OUTPUT_PORT1, p1)

def tca9539_config(bus):
    return (
        tca_write(bus, REG_OUTPUT_PORT0, 0x00) &
        tca_write(bus, REG_OUTPUT_PORT1, 0x00) &
        tca_write(bus, REG_CONFIG_PORT0, 0x00) &
        tca_write(bus, REG_CONFIG_PORT1, 0x00)
    )

def enable_r1(bus): return update_io_expander(bus, R1_PORT0, R1_PORT1)
def enable_r2(bus): return update_io_expander(bus, R2_PORT0, R2_PORT1)
def disable_all(bus): return update_io_expander(bus, 0x00, 0x00)

# ==========================================================
# ADC Read + Log
# ==========================================================
def read_all_channels(bus, current_file, group):
    writer = get_csv_writer(current_file)

    for ch in range(2, 7):
        ads_write_reg(bus, REG_CHANNEL_SEL, ch)

        if ads_read_reg(bus, REG_CHANNEL_SEL) != ch:
            continue

        raw, voltage, dose = ads_read_adc(bus)

        if raw is None:
            continue

        writer.writerow([
            datetime.now().isoformat(),
            group,
            ch - 1,
            raw,
            voltage,
            dose
        ])

        current_file["file"].flush()
        time.sleep(0.01)

# ==========================================================
# Main
# ==========================================================
current_file = {"file": None, "writer": None, "name": None}

with SMBus(ADS_BUS) as ads_bus, SMBus(TCA_BUS) as tca_bus:
    tca9539_config(tca_bus)
    time.sleep(0.05)

    enable_r1(tca_bus)
    time.sleep(0.2)
    read_all_channels(ads_bus, current_file, "R1")
    disable_all(tca_bus)

    time.sleep(0.5)

    enable_r2(tca_bus)
    time.sleep(0.2)
    read_all_channels(ads_bus, current_file, "R2")
    disable_all(tca_bus)

if current_file["file"]:
    current_file["file"].close()

print("\nDone. Logs stored in ./radfet_logs/\n")