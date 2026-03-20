#!/usr/bin/env python3
import time
from smbus2 import SMBus

# -----------------------------
# DEVICE CONFIG
# -----------------------------
I2C_BUS = 7
ADS_ADDR = 0x10
VREF = 3.3  # Reference voltage

# ADS7138 Registers
REG_SYSTEM_STATUS  = 0x00
REG_GENERAL_CFG    = 0x01
REG_DATA_CFG       = 0x02
REG_OSR_CFG        = 0x03
REG_OPMODE_CFG     = 0x04
REG_PIN_CFG        = 0x05
REG_SEQUENCE_CFG   = 0x10
REG_CHANNEL_SEL    = 0x11
REG_RECENT_CH0_LSB = 0xA0  # Base address for recent channel data

# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def write_reg8(bus, reg, val):
    """Write a byte and read it back to verify"""
    try:
        bus.write_byte_data(ADS_ADDR, reg, val)
    except Exception as e:
        print(f"WRITE 0x{reg:02X} = 0x{val:02X} FAIL ({e})")
        return False
    # Read back to verify
    try:
        read_val = bus.read_byte_data(ADS_ADDR, reg)
        if read_val == val:
            print(f"WRITE 0x{reg:02X} = 0x{val:02X} PASS")
            return True
        else:
            print(f"WRITE 0x{reg:02X} = 0x{val:02X} FAIL (read back 0x{read_val:02X})")
            return False
    except Exception as e:
        print(f"VERIFY 0x{reg:02X} FAIL ({e})")
        return False

def read_reg8(bus, reg):
    try:
        return bus.read_byte_data(ADS_ADDR, reg)
    except Exception as e:
        print(f"READ 0x{reg:02X} FAIL ({e})")
        return None

def read_adc(bus, ch):
    """Read 12-bit ADC value from RECENT_CHn registers"""
    base = REG_RECENT_CH0_LSB + ch * 2
    lsb = read_reg8(bus, base)
    msb = read_reg8(bus, base + 1)
    if lsb is None or msb is None:
        return None, None
    raw = ((msb << 8) | lsb) >> 4  # 12-bit result
    voltage = (raw / 4095.0) * VREF
    return raw, voltage

# -----------------------------
# MAIN LOOP
# -----------------------------
with SMBus(I2C_BUS) as bus:
    # --- Initialize ADS7138 in manual mode ---
    init_success = True
    init_success &= write_reg8(bus, REG_PIN_CFG, 0x00)       # All pins analog
    init_success &= write_reg8(bus, REG_SEQUENCE_CFG, 0x00)  # Manual mode
    init_success &= write_reg8(bus, REG_GENERAL_CFG, 0x00)   # Default config
    init_success &= write_reg8(bus, REG_OPMODE_CFG, 0x00)    # Manual single-shot
    init_success &= write_reg8(bus, REG_DATA_CFG, 0x10)      # Include channel ID
    time.sleep(0.05)

    if not init_success:
        print("Initialization had errors; continuing anyway...")

    print("Starting continuous 8-channel scan with verified writes...\n")

    try:
        while True:
            results = []
            for ch in range(8):
                # Select channel
                write_reg8(bus, REG_CHANNEL_SEL, ch)
                # Read ADC
                raw, voltage = read_adc(bus, ch)
                if raw is None:
                    results.append(f"CH{ch}: read fail")
                else:
                    results.append(f"CH{ch}: {raw} -> {voltage:.3f} V")
            # Print all channels together
            print(" | ".join(results))
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\nScan stopped by user")


