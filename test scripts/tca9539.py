#!/usr/bin/env python3
import time
from smbus2 import SMBus

# -----------------------------
# DEVICE CONFIG
# -----------------------------
I2C_BUS = 1
TCA9539_ADDR = 0x74

# TCA9539 registers
REG_OUTPUT_PORT0 = 0x02
REG_OUTPUT_PORT1 = 0x03
REG_CONFIG_PORT0 = 0x06
REG_CONFIG_PORT1 = 0x07

# -----------------------------
# BIT DEFINITIONS
# -----------------------------
# Port0
P00_D3_EN = 1 << 0
P01_D3_R1 = 1 << 1
P02_D2_R2 = 1 << 2
P03_D2_EN = 1 << 3
P04_D2_R1 = 1 << 4
P05_D1_R2 = 1 << 5
P06_D1_EN = 1 << 6
P07_D1_R1 = 1 << 7

# Port1
P11_D5_R1 = 1 << 1
P12_D5_EN = 1 << 2
P13_D5_R2 = 1 << 3
P14_D4_R1 = 1 << 4
P15_D4_EN = 1 << 5
P16_D4_R2 = 1 << 6
P17_D3_R2 = 1 << 7

# -----------------------------
# I2C HELPER FUNCTIONS
# -----------------------------
def write_reg8(bus, reg, val):
    try:
        bus.write_byte_data(TCA9539_ADDR, reg, val)
        print(f"WRITE 0x{reg:02X} = 0x{val:02X} SENT")
        return True
    except Exception as e:
        print(f"WRITE 0x{reg:02X} FAIL ({e})")
        return False

def update_io_expander(bus, port0, port1):
    ok0 = write_reg8(bus, REG_OUTPUT_PORT0, port0)
    ok1 = write_reg8(bus, REG_OUTPUT_PORT1, port1)
    return ok0 and ok1

# -----------------------------
# TCA9539 CONFIG
# -----------------------------
def tca9539_config(bus):
    write_reg8(bus, REG_OUTPUT_PORT0, 0x00)
    write_reg8(bus, REG_OUTPUT_PORT1, 0x00)
    write_reg8(bus, REG_CONFIG_PORT0, 0x00)  # all outputs
    write_reg8(bus, REG_CONFIG_PORT1, 0x00)  # all outputs
    print("TCA9539 configured\n")

# -----------------------------
# RADFET CONTROL
# -----------------------------
def enable_all_r1(bus):
    port0 = P00_D3_EN | P06_D1_EN | P03_D2_EN | P01_D3_R1 | P07_D1_R1 | P04_D2_R1
    port1 = P15_D4_EN | P12_D5_EN | P14_D4_R1 | P11_D5_R1
    print("Enabling R1 sensors")
    update_io_expander(bus, port0, port1)
    time.sleep(0.2)

def enable_all_r2(bus):
    port0 = P00_D3_EN | P06_D1_EN | P03_D2_EN | P05_D1_R2 | P02_D2_R2
    port1 = P15_D4_EN | P12_D5_EN | P17_D3_R2 | P16_D4_R2 | P13_D5_R2
    print("Enabling R2 sensors")
    update_io_expander(bus, port0, port1)
    time.sleep(0.2)

def disable_all(bus):
    print("Disabling all sensors")
    update_io_expander(bus, 0x00, 0x00)

# -----------------------------
# MAIN SEQUENCE (RUN ONCE)
# -----------------------------
with SMBus(I2C_BUS) as bus:
    tca9539_config(bus)

    enable_all_r1(bus)
    disable_all(bus)

    enable_all_r2(bus)
    disable_all(bus)

    print("\nSequence completed")

