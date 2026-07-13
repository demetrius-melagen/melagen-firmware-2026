#!/usr/bin/env python3

import csv
import os
import time
from datetime import datetime

from smbus2 import SMBus, i2c_msg


# ==========================================================
# Logging Configuration
# ==========================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRIMARY_LOG_DIR = "radfet_logs"
BACKUP_LOG_DIR = "backup_logs"
ERROR_LOG_FILE = os.path.join(SCRIPT_DIR, "melagen_error_log.csv")


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
# ADS7138 Opcodes and Registers
# ==========================================================
ADS_OPCODE_READ = 0x10
ADS_OPCODE_WRITE = 0x08
REG_CHANNEL_SEL = 0x11


# ==========================================================
# TCA9539 Registers
# ==========================================================
REG_OUTPUT_PORT0 = 0x02
REG_OUTPUT_PORT1 = 0x03
REG_CONFIG_PORT0 = 0x06
REG_CONFIG_PORT1 = 0x07


# ==========================================================
# Bit Definitions
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

R1_PORT0 = (
    P00_FET1_CTL | P01_FET1_R1 |
    P03_FET2_CTL | P04_FET2_R1 |
    P06_FET3_CTL | P07_FET3_R1
)
R1_PORT1 = (
    P11_FET4_CTL | P12_FET4_R1 |
    P14_FET5_CTL | P15_FET5_R1
)
R2_PORT0 = (
    P00_FET1_CTL | P02_FET1_R2 |
    P03_FET2_CTL | P05_FET2_R2 |
    P06_FET3_CTL
)
R2_PORT1 = (
    P10_FET3_R2 |
    P11_FET4_CTL | P13_FET4_R2 |
    P14_FET5_CTL | P16_FET5_R2
)


# ==========================================================
# Error Logging
# ==========================================================
def write_error_log(error_code, error_message):
    """Append an error to the CSV stored beside this script."""
    try:
        file_exists = os.path.isfile(ERROR_LOG_FILE)
        with open(ERROR_LOG_FILE, "a", newline="", encoding="utf-8") as error_file:
            writer = csv.writer(error_file)
            if not file_exists or os.path.getsize(ERROR_LOG_FILE) == 0:
                writer.writerow(["time", "error code", "error message"])
            writer.writerow([
                datetime.now().isoformat(timespec="seconds"),
                error_code,
                str(error_message),
            ])
            error_file.flush()
    except Exception as error:
        # This function cannot call itself if the error log is unavailable.
        print(f"[ERROR LOG FAILURE] {type(error).__name__}: {error}")


# ==========================================================
# Daily CSV File Management
# ==========================================================
def build_daily_filename(base_dir):
    """Return one fixed sensor-log filename for the current calendar day."""
    try:
        os.makedirs(base_dir, exist_ok=True)
        date_string = datetime.now().strftime("%Y-%m-%d")
        return os.path.join(base_dir, f"radfet_{date_string}.csv")
    except Exception as error:
        write_error_log("LOG_DIR_ERROR", f"Cannot access {base_dir}: {error}")
        return None


def open_log_file(filename):
    try:
        file_exists = os.path.isfile(filename)
        log_file = open(filename, "a", newline="", encoding="utf-8")
        writer = csv.writer(log_file)
        if not file_exists or os.path.getsize(filename) == 0:
            writer.writerow([
                "timestamp",
                "sensor_group",
                "channel",
                "raw_adc",
                "voltage",
            ])
            log_file.flush()
        return {"file": log_file, "writer": writer, "name": filename}
    except Exception as error:
        write_error_log("LOG_OPEN_ERROR", f"Cannot open {filename}: {error}")
        return {"file": None, "writer": None, "name": filename}


def initialize_loggers():
    try:
        empty_logger = {"file": None, "writer": None, "name": None}
        return {"primary": empty_logger.copy(), "backup": empty_logger.copy()}
    except Exception as error:
        write_error_log("LOGGER_INIT_ERROR", error)
        return {
            "primary": {"file": None, "writer": None, "name": None},
            "backup": {"file": None, "writer": None, "name": None},
        }


def safe_close_logger(logger, logger_name):
    try:
        if logger.get("file") is not None:
            logger["file"].close()
        logger["file"] = None
        logger["writer"] = None
        return True
    except Exception as error:
        write_error_log("LOG_CLOSE_ERROR", f"{logger_name}: {error}")
        logger["file"] = None
        logger["writer"] = None
        return False


def safe_write_logger(logger, row, logger_name):
    try:
        if logger.get("file") is None or logger.get("writer") is None:
            return False
        logger["writer"].writerow(row)
        logger["file"].flush()
        return True
    except Exception as error:
        write_error_log("LOG_WRITE_ERROR", f"{logger_name}: {error}")
        safe_close_logger(logger, logger_name)
        return False


def update_log_files(loggers):
    """Open or rotate sensor logs when the calendar day changes."""
    try:
        destinations = {
            "primary": build_daily_filename(PRIMARY_LOG_DIR),
            "backup": build_daily_filename(BACKUP_LOG_DIR),
        }

        for logger_name, filename in destinations.items():
            logger = loggers[logger_name]
            if filename is None:
                safe_close_logger(logger, logger_name)
                continue
            if logger.get("name") != filename or logger.get("file") is None:
                safe_close_logger(logger, logger_name)
                loggers[logger_name] = open_log_file(filename)
        return True
    except Exception as error:
        write_error_log("LOG_UPDATE_ERROR", error)
        return False


def write_log_row(loggers, row):
    try:
        update_log_files(loggers)
        primary_ok = safe_write_logger(loggers["primary"], row, "PRIMARY")
        backup_ok = safe_write_logger(loggers["backup"], row, "BACKUP")
        if not primary_ok and not backup_ok:
            write_error_log(
                "ALL_LOGS_UNAVAILABLE",
                "Unable to write to primary or backup sensor log",
            )
        return primary_ok or backup_ok
    except Exception as error:
        write_error_log("LOG_ROW_ERROR", error)
        return False


# ==========================================================
# ADS Functions
# ==========================================================
def ads_write_reg(bus, reg, val):
    try:
        message = i2c_msg.write(ADS_ADDR, [ADS_OPCODE_WRITE, reg, val])
        bus.i2c_rdwr(message)
        return True
    except Exception as error:
        write_error_log(
            "ADS_WRITE_ERROR",
            f"ADDR=0x{ADS_ADDR:02X}, REG=0x{reg:02X}, VAL=0x{val:02X}: {error}",
        )
        return False


def ads_read_reg(bus, reg):
    try:
        command = i2c_msg.write(ADS_ADDR, [ADS_OPCODE_READ, reg])
        response = i2c_msg.read(ADS_ADDR, 1)
        bus.i2c_rdwr(command, response)
        return list(response)[0]
    except Exception as error:
        write_error_log(
            "ADS_REGISTER_READ_ERROR",
            f"ADDR=0x{ADS_ADDR:02X}, REG=0x{reg:02X}: {error}",
        )
        return None


def ads_read_adc(bus):
    """Read the raw ADC value and convert it directly to volts."""
    try:
        response = i2c_msg.read(ADS_ADDR, 2)
        bus.i2c_rdwr(response)
        data = list(response)
        raw_adc = ((data[0] << 8) | data[1]) >> 4
        voltage = raw_adc * VREF / 4095.0
        return raw_adc, voltage
    except Exception as error:
        write_error_log("ADC_READ_ERROR", error)
        return None, None


# ==========================================================
# TCA Control
# ==========================================================
def tca_write(bus, reg, val):
    try:
        bus.write_byte_data(TCA9539_ADDR, reg, val)
        return True
    except Exception as error:
        write_error_log(
            "TCA_WRITE_ERROR",
            f"ADDR=0x{TCA9539_ADDR:02X}, REG=0x{reg:02X}, VAL=0x{val:02X}: {error}",
        )
        return False


def update_io_expander(bus, port0, port1):
    try:
        success0 = tca_write(bus, REG_OUTPUT_PORT0, port0)
        success1 = tca_write(bus, REG_OUTPUT_PORT1, port1)
        if not (success0 and success1):
            write_error_log(
                "IO_EXPANDER_ERROR",
                f"Failed to set P0=0x{port0:02X}, P1=0x{port1:02X}",
            )
        return success0 and success1
    except Exception as error:
        write_error_log("IO_EXPANDER_ERROR", error)
        return False


def tca9539_config(bus):
    try:
        print("\nConfiguring TCA9539 IO expander...")
        results = [
            tca_write(bus, REG_OUTPUT_PORT0, 0x00),
            tca_write(bus, REG_OUTPUT_PORT1, 0x00),
            tca_write(bus, REG_CONFIG_PORT0, 0x00),
            tca_write(bus, REG_CONFIG_PORT1, 0x00),
        ]
        success = all(results)
        print("TCA9539 configuration successful" if success else "TCA9539 configuration FAILED")
        if not success:
            write_error_log("TCA_CONFIG_ERROR", "One or more configuration writes failed")
        return success
    except Exception as error:
        write_error_log("TCA_CONFIG_ERROR", error)
        return False


def enable_r1(bus):
    try:
        print("\nEnabling R1 sensor bank...")
        return update_io_expander(bus, R1_PORT0, R1_PORT1)
    except Exception as error:
        write_error_log("ENABLE_R1_ERROR", error)
        return False


def enable_r2(bus):
    try:
        print("\nEnabling R2 sensor bank...")
        return update_io_expander(bus, R2_PORT0, R2_PORT1)
    except Exception as error:
        write_error_log("ENABLE_R2_ERROR", error)
        return False


def disable_all(bus):
    try:
        print("\nDisabling all sensors...")
        return update_io_expander(bus, 0x00, 0x00)
    except Exception as error:
        write_error_log("DISABLE_ALL_ERROR", error)
        return False


# ==========================================================
# ADC Read + Log
# ==========================================================
def read_all_channels(bus, loggers, group):
    try:
        print(f"\nReading sensor group: {group}")
        for channel in range(2, 7):
            try:
                print(f"\nSelecting ADS channel {channel}")
                if not ads_write_reg(bus, REG_CHANNEL_SEL, channel):
                    continue

                verified_channel = ads_read_reg(bus, REG_CHANNEL_SEL)
                if verified_channel != channel:
                    write_error_log(
                        "CHANNEL_VERIFY_ERROR",
                        f"Wrote CH{channel} but read back {verified_channel}",
                    )
                    continue

                raw_adc, voltage = ads_read_adc(bus)
                if raw_adc is None:
                    continue

                print(f"CH{channel - 1} | RAW={raw_adc} | V={voltage:.6f} V")
                write_log_row(loggers, [
                    datetime.now().isoformat(),
                    group,
                    channel - 1,
                    raw_adc,
                    voltage,
                ])
                time.sleep(0.01)
            except Exception as error:
                write_error_log(
                    "CHANNEL_READ_ERROR",
                    f"Group {group}, channel {channel}: {error}",
                )
                continue
        return True
    except Exception as error:
        write_error_log("READ_ALL_CHANNELS_ERROR", f"Group {group}: {error}")
        return False


def close_all_loggers(loggers):
    try:
        print("\nCleaning up log files...")
        for logger_name, logger in loggers.items():
            filename = logger.get("name")
            if logger.get("file") is not None:
                safe_close_logger(logger, logger_name)
                print(f"Closed: {os.path.abspath(filename)}")
        return True
    except Exception as error:
        write_error_log("LOGGER_CLEANUP_ERROR", error)
        return False


def main():
    try:
        print("\n========================================")
        print("RADFET ADC LOGGER STARTING")
        print("========================================")

        loggers = initialize_loggers()
        try:
            with SMBus(ADS_BUS) as ads_bus, SMBus(TCA_BUS) as tca_bus:
                print("\nOpened I2C buses successfully")

                if tca9539_config(tca_bus):
                    time.sleep(0.05)

                    if enable_r1(tca_bus):
                        time.sleep(0.2)
                        read_all_channels(ads_bus, loggers, "R1")
                    disable_all(tca_bus)
                    time.sleep(0.5)

                    if enable_r2(tca_bus):
                        time.sleep(0.2)
                        read_all_channels(ads_bus, loggers, "R2")
                    disable_all(tca_bus)
                else:
                    write_error_log(
                        "TCA_CONFIG_FATAL",
                        "Sensor reads skipped because TCA9539 configuration failed",
                    )
        except Exception as error:
            write_error_log("MAIN_I2C_ERROR", error)
            print(f"\n[I2C ERROR] {type(error).__name__}: {error}")
        finally:
            close_all_loggers(loggers)

        print("\n========================================")
        print("DONE")
        print("========================================")
        print(f"\nPrimary logs: {os.path.abspath(PRIMARY_LOG_DIR)}")
        print(f"Backup logs : {os.path.abspath(BACKUP_LOG_DIR)}")
        print(f"Error log   : {ERROR_LOG_FILE}\n")
        return 0
    except Exception as error:
        write_error_log("MAIN_ERROR", error)
        print(f"[MAIN ERROR] {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    main()
