#!/usr/bin/env python3
import time
from smbus2 import SMBus, i2c_msg
import csv
import os
from datetime import datetime
CSV_FILE = "radfet_measurements.csv"

# ==========================================================
# Dosimeter Calibration Variables
# ==========================================================
A=0.02951
B=0.45509

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
# TCA9539 -> Dosimeter Bit Definitions
# ==========================================================
P00_FET1_CTL = 1 << 0
P01_FET1_R1 = 1 << 1
P02_FET1_R2 = 1 << 2
P03_FET2_CTL = 1 << 3
P04_FET2_R1 = 1 << 4
P05_FET2_R2 = 1 << 5
P06_FET3_CTL = 1 << 6
P07_FET3_R1 = 1 << 7

P10_FET3_R2 = 1 << 0
P11_FET4_CTL = 1 << 1
P12_FET4_R1 = 1 << 2
P13_FET4_R2 = 1 << 3
P14_FET5_CTL = 1 << 4
P15_FET5_R1 = 1 << 5
P16_FET5_R2 = 1 << 6

# ==========================================================
# ADS7138 Low Level Commands
# ==========================================================
def ads_write_reg(bus, reg, val):
    try:
        msg = i2c_msg.write(ADS_ADDR, [ADS_OPCODE_WRITE, reg, val])
        bus.i2c_rdwr(msg)
        print(f"ADS WR 0x{reg:02X}=0x{val:02X} PASS")
        return True
    except Exception as e:
        print(f"ADS WR FAIL {e}")
        return False


def ads_read_reg(bus, reg):
    try:
        cmd = i2c_msg.write(ADS_ADDR, [ADS_OPCODE_READ, reg])
        read = i2c_msg.read(ADS_ADDR, 1)
        bus.i2c_rdwr(cmd, read)
        val = list(read)[0]
        print(f"ADS RD 0x{reg:02X}->0x{val:02X} PASS")
        return val

    except Exception as e:
        print("ADS RD FAIL", e)
        return None


def ads_read_adc(bus):
    try:
        read = i2c_msg.read(ADS_ADDR, 2)
        bus.i2c_rdwr(read)
        data = list(read)
        raw = ((data[0] << 8) | data[1]) >> 4
        voltage = raw * VREF / 4095.0
        dose = (voltage / A) ** (1.0 / B)
    	# V = A * Dose^B  => Dose = (V/A)^(1/B)
        return raw, voltage, dose

    except Exception as e:
        print("ADC READ FAIL", e)
        return None, None, None


# ==========================================================
# TCA9539 Control
# ==========================================================
def tca_write(bus, reg, val):
    try:
        bus.write_byte_data(TCA9539_ADDR, reg, val)
        print(f"TCA WR 0x{reg:02X}=0x{val:02X} PASS")
        return True
    except Exception as e:
        print("TCA WRITE FAIL", e)
        return False

def tca_read(bus, reg, val):
    try:
        bus.read_byte_data(TCA9539_ADDR, reg, val)
        print(f"TCA RD 0x{reg:02X}=0x{val:02X} PASS")
        return True
    except Exception as e:
        print("TCA READ FAIL", e)
        return False


def update_io_expander(bus, port0, port1):
    return tca_write(bus, REG_OUTPUT_PORT0, port0) & tca_write(bus, REG_OUTPUT_PORT1, port1)

def tca9539_config(bus):

    print("\nInitializing TCA9539")

    return tca_write(bus, REG_OUTPUT_PORT0, 0x00) & \
    tca_write(bus, REG_OUTPUT_PORT1, 0x00) & \
    tca_write(bus, REG_CONFIG_PORT0, 0x00) & \
    tca_write(bus, REG_CONFIG_PORT1, 0x00)


def enable_r1(bus):

    print("\nEnabling R1 sensors")

    port0 = P00_FET1_CTL | P01_FET1_R1 | P03_FET2_CTL | P04_FET2_R1 | P06_FET3_CTL | P07_FET3_R1
    port1 = P11_FET4_CTL | P12_FET4_R1 | P14_FET5_CTL | P15_FET5_R1

    update_io_expander(bus, port0, port1)

    time.sleep(0.2)


def enable_r2(bus):

    print("\nEnabling R2 sensors")

    port0 = P00_FET1_CTL | P02_FET1_R2 | P03_FET2_CTL | P05_FET2_R2 | P06_FET3_CTL
    port1 = P10_FET3_R2 | P11_FET4_CTL | P13_FET4_R2 | P14_FET5_CTL | P16_FET5_R2

    update_io_expander(bus, port0, port1)

    time.sleep(0.2)


def disable_all(bus):

    print("\nDisabling sensors")

    update_io_expander(bus, 0x00, 0x00)


# ==========================================================
# ADS7138 INIT
# ==========================================================
def ads7138_init(bus):

    print("\nInitializing ADS7138")

    ads_write_reg(bus, REG_PIN_CFG, 0x00)
    ads_write_reg(bus, REG_SEQUENCE_CFG, 0x00)
    ads_write_reg(bus, REG_GENERAL_CFG, 0x00)
    ads_write_reg(bus, REG_OPMODE_CFG, 0x00)
    ads_write_reg(bus, REG_DATA_CFG, 0x10)

    time.sleep(0.05)

# ==========================================================
# ADC Channel Read + CSV Save
# ==========================================================
def read_all_channels(bus, csv_writer, sensor_group):

	print("\nADC READINGS")
	results=[]
	for ch in range(2,7):

		ads_write_reg(bus, REG_CHANNEL_SEL, ch)
		confirm = ads_read_reg(bus, REG_CHANNEL_SEL)

		if confirm != ch:
			results.append(f"Change to CH{ch-1}:FAIL")
			continue	

		raw, voltage, dose = ads_read_adc(bus)

		if raw is None:
			results.append(f"CH{ch-1}:FAIL")
		else:
			results.append(f"CH{ch-1}:{raw} ({voltage:.3f}V) ({dose:.3f}rad)")
			
			csv_writer.writerow([
				datetime.now().isoformat(),
				sensor_group,
				(ch-1),
				raw,
				voltage,
				dose
			])

		time.sleep(0.01)
	print("|".join(results))

# ==========================================================
# Main Sequence
# ==========================================================

file_exists = os.path.isfile(CSV_FILE)

with open(CSV_FILE, "a", newline="") as f:

    writer = csv.writer(f)
    # write header only if file did not exist
    if not file_exists:
        writer.writerow([
            "timestamp",
            "sensor_group",
            "channel",
            "raw_adc",
            "voltage",
            "dose_rad"
        ])

    with SMBus(ADS_BUS) as ads_bus, SMBus(TCA_BUS) as tca_bus:
        print("\nSystem Init")
        tca9539_config(tca_bus)
        ads7138_init(ads_bus)
        print("\nR1 Measurement")
        enable_r1(tca_bus)
        read_all_channels(ads_bus, writer, "R1")
        disable_all(tca_bus)
        time.sleep(0.5)
        print("\nR2 Measurement")
        enable_r2(tca_bus)
        read_all_channels(ads_bus, writer, "R2")
        disable_all(tca_bus)

print("\nSequence complete")
print(f"Data saved to {CSV_FILE}\n")