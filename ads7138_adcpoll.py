#!/usr/bin/env python3
from smbus2 import SMBus
import time

# I2C bus & device
I2C_BUS = 1
ADS_ADDR = 0x10

# Registers
REG_SYSTEM_STATUS = 0x00
REG_GENERAL_CFG   = 0x01
REG_DATA_CFG      = 0x02
REG_OSR_CFG       = 0x03
REG_OPMODE_CFG    = 0x04
REG_PIN_CFG       = 0x05
REG_SEQUENCE_CFG  = 0x10
REG_CHANNEL_SEL   = 0x11
REG_RECENT_CH0_LSB = 0xA0  # Base for recent channel data

VREF = 3.3  # Reference voltage

def write_reg8(bus, reg, val):
    bus.write_byte_data(ADS_ADDR, reg, val)

def read_reg8(bus, reg):
    return bus.read_byte_data(ADS_ADDR, reg)

def read_adc(bus, ch):
    """Read 12-bit ADC value from RECENT_CHn registers"""
    base = REG_RECENT_CH0_LSB + ch * 2
    lsb = read_reg8(bus, base)
    msb = read_reg8(bus, base + 1)
    raw = ((msb << 8) | lsb) >> 4  # 12-bit value
    voltage = (raw / 4095.0) * VREF
    return raw, voltage

def wait_ready(bus, timeout=0.005):
    """Poll SYSTEM_STATUS for ready (optional, BP-ADS7128 may always be ready)"""
    start = time.time()
    while time.time() - start < timeout:
        status = read_reg8(bus, REG_SYSTEM_STATUS)
        if status & 0x01 == 0:  # Conversion ready
            return True
        time.sleep(0.001)
    return False

with SMBus(I2C_BUS) as bus:
    # --- Initialize ADS in manual mode ---
    write_reg8(bus, REG_PIN_CFG, 0x00)       # All pins analog input
    write_reg8(bus, REG_SEQUENCE_CFG, 0x00)  # Manual mode
    write_reg8(bus, REG_GENERAL_CFG, 0x00)   # Default config
    write_reg8(bus, REG_OPMODE_CFG, 0x00)    # Manual mode
    write_reg8(bus, REG_DATA_CFG, 0x10)      # Include channel ID in result
    time.sleep(0.05)

    print("Starting continuous 8-channel scan...\n")
    try:
        while True:
            results = []
            for ch in range(8):
                write_reg8(bus, REG_CHANNEL_SEL, ch)
                #if not wait_ready(bus):
                   # print(f"CH{ch}: conversion not ready, reading anyway")
                raw, voltage = read_adc(bus, ch)
                results.append(f"CH{ch}: {raw} -> {voltage:.3f} V")
            # Print all channels together
            print(" | ".join(results))
            time.sleep(0.5)  # half-second interval between scans
    except KeyboardInterrupt:
        print("\nScan stopped by user")

