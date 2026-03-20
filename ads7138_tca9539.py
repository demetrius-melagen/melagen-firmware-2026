#!/usr/bin/env python3
from smbus2 import SMBus
import time

# ============================================================
# BUS CONFIGURATION
# ============================================================
TCA_BUS = 1
ADC_BUS = 7

TCA_ADDR = 0x74
ADS_ADDR = 0x10

VREF = 3.3

# ============================================================
# TCA9539 REGISTERS
# ============================================================
TCA_OUT_PORT0 = 0x02
TCA_OUT_PORT1 = 0x03
TCA_CFG_PORT0 = 0x06
TCA_CFG_PORT1 = 0x07

# ============================================================
# ADS7138 REGISTERS (Complete set used)
# ============================================================
REG_SYSTEM_STATUS  = 0x00
REG_GENERAL_CFG    = 0x01
REG_DATA_CFG       = 0x02
REG_OSR_CFG        = 0x03
REG_OPMODE_CFG     = 0x04
REG_PIN_CFG        = 0x05
REG_GPIO_CFG       = 0x07
REG_GPO_DRIVE_CFG  = 0x09
REG_GPO_VALUE      = 0x0B
REG_SEQUENCE_CFG   = 0x10
REG_CHANNEL_SEL    = 0x11

REG_RECENT_CH0_LSB = 0xA0  # Base address for recent conversion results

# ============================================================
# RADFET BIT DEFINITIONS
# ============================================================

# Port 0
P00_D3_EN = (1 << 0)
P01_D3_R1 = (1 << 1)
P02_D2_R2 = (1 << 2)
P03_D2_EN = (1 << 3)
P04_D2_R1 = (1 << 4)
P05_D1_R2 = (1 << 5)
P06_D1_EN = (1 << 6)
P07_D1_R1 = (1 << 7)

# Port 1
P11_D5_R1 = (1 << 1)
P12_D5_EN = (1 << 2)
P13_D5_R2 = (1 << 3)
P14_D4_R1 = (1 << 4)
P15_D4_EN = (1 << 5)
P16_D4_R2 = (1 << 6)
P17_D3_R2 = (1 << 7)

# ============================================================
# TCA FUNCTIONS (BUS 1)
# ============================================================

def tca_write(bus, reg, val):
    bus.write_byte_data(TCA_ADDR, reg, val)

def tca_config(bus):
    # All outputs low
    tca_write(bus, TCA_OUT_PORT0, 0x00)
    tca_write(bus, TCA_OUT_PORT1, 0x00)

    # All pins configured as outputs
    tca_write(bus, TCA_CFG_PORT0, 0x00)
    tca_write(bus, TCA_CFG_PORT1, 0x00)

def radfet_enable_all(bus, r):
    port0 = 0
    port1 = 0

    # Common enable lines
    port0 |= (P00_D3_EN | P06_D1_EN | P03_D2_EN)
    port1 |= (P15_D4_EN | P12_D5_EN)

    if r == 0:  # R1
        port0 |= (P01_D3_R1 | P07_D1_R1 | P04_D2_R1)
        port1 |= (P14_D4_R1 | P11_D5_R1)

    elif r == 1:  # R2
        port0 |= (P05_D1_R2 | P02_D2_R2)
        port1 |= (P17_D3_R2 | P16_D4_R2 | P13_D5_R2)

    else:
        raise ValueError("Invalid rail selection")

    tca_write(bus, TCA_OUT_PORT0, port0)
    tca_write(bus, TCA_OUT_PORT1, port1)

    print(f"\nR{r+1} ENABLED  | PORT0=0x{port0:02X} PORT1=0x{port1:02X}")

    time.sleep(0.2)  # Hardware settle time

def radfet_disable_all(bus):
    tca_write(bus, TCA_OUT_PORT0, 0x00)
    tca_write(bus, TCA_OUT_PORT1, 0x00)

# ============================================================
# ADS7138 FUNCTIONS (BUS 7)
# ============================================================

def ads_write(bus, reg, val):
    bus.write_byte_data(ADS_ADDR, reg, val)

def ads_read(bus, reg):
    return bus.read_byte_data(ADS_ADDR, reg)

def ads_wait_ready(bus, timeout=0.01):
    start = time.time()
    while time.time() - start < timeout:
        status = ads_read(bus, REG_SYSTEM_STATUS)
        if (status & 0x01) == 0:
            return True
        time.sleep(0.001)
    return False

def ads_init(bus):
    print("Initializing ADS7138...")

    ads_write(bus, REG_PIN_CFG, 0x00)        # All pins analog
    ads_write(bus, REG_GPIO_CFG, 0x00)       # No GPIO
    ads_write(bus, REG_GPO_DRIVE_CFG, 0x00)  # Push-pull default
    ads_write(bus, REG_GPO_VALUE, 0x00)      # GPO low

    ads_write(bus, REG_SEQUENCE_CFG, 0x00)   # Manual mode
    ads_write(bus, REG_GENERAL_CFG, 0x00)    # Default
    ads_write(bus, REG_OPMODE_CFG, 0x00)     # Manual conversion
    ads_write(bus, REG_OSR_CFG, 0x00)        # Default OSR
    ads_write(bus, REG_DATA_CFG, 0x10)       # Include channel ID

    time.sleep(0.05)

def ads_read_channel(bus, ch):
    ads_write(bus, REG_CHANNEL_SEL, ch)

    ads_wait_ready(bus)

    base = REG_RECENT_CH0_LSB + ch * 2
    lsb = ads_read(bus, base)
    msb = ads_read(bus, base + 1)

    raw = ((msb << 8) | lsb) >> 4
    voltage = (raw / 4095.0) * VREF

    return raw, voltage

# ============================================================
# MAIN LOOP
# ============================================================

with SMBus(TCA_BUS) as tca_bus, SMBus(ADC_BUS) as adc_bus:

    print("Configuring TCA9539...")
    tca_config(tca_bus)

    ads_init(adc_bus)

    try:
        while True:

            # ===================== R1 =====================
            radfet_enable_all(tca_bus, 0)

            r1_results = []
            for ch in range(8):
                raw, voltage = ads_read_channel(adc_bus, ch)
                r1_results.append(f"R1-CH{ch}:{voltage:.3f}V")

            print(" | ".join(r1_results))

            # ===================== R2 =====================
            radfet_enable_all(tca_bus, 1)

            r2_results = []
            for ch in range(8):
                raw, voltage = ads_read_channel(adc_bus, ch)
                r2_results.append(f"R2-CH{ch}:{voltage:.3f}V")

            print(" | ".join(r2_results))

            time.sleep(0.5)

    except KeyboardInterrupt:
        radfet_disable_all(tca_bus)
        print("\nStopped safely")

