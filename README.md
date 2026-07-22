# VT01 Dosimeter Sensor Data Acquisition

Firmware-style Python software for controlling a sensor data collection loop using an **ADS7138 analog-to-digital converter (ADC)** and a **TCA9539 I/O expander** with **VT01 dosimeters**.

The application selects two VT01 sensor banks (`R1` and `R2`) through the TCA9539, samples five ADC inputs from each bank through the ADS7138, and writes every valid measurement to redundant daily CSV logs. Hardware and logging failures are captured separately in an error log so that acquisition problems can be diagnosed without stopping the entire channel scan.

> **Note:** Although this project performs a firmware-like hardware-control role, the supplied implementation is a Python 3 program intended to run under Linux on a host with accessible I²C buses, such as a Raspberry Pi or another single-board computer.

## Features

- Controls five VT01 dosimeter positions divided into `R1` and `R2` sensor banks.
- Uses the TCA9539 to enable one sensor bank at a time.
- Reads five ADS7138 inputs for each bank, producing ten measurement rows per successful run.
- Verifies the ADC channel-selection register before accepting a conversion.
- Converts the ADS7138 12-bit code directly to voltage using a configurable reference voltage.
- Creates date-based CSV files and automatically rotates them when the calendar date changes.
- Writes every measurement to both primary and backup logs.
- Flushes each row immediately to reduce data loss if the system loses power.
- Records structured error codes, timestamps, and messages in a dedicated CSV file.
- Attempts to disable all dosimeters between banks and after acquisition.
- Isolates per-channel failures so a problem on one input does not prevent the remaining inputs from being sampled.

## Hardware Overview

The application communicates with two I²C devices on separate Linux I²C buses:

| Device | Purpose | I²C bus | Address |
|---|---|---:|---:|
| ADS7138 | Selects and digitizes dosimeter analog signals | `1` | `0x10` |
| TCA9539 | Controls the R1/R2 routing and enable lines | `7` | `0x74` |

The ADC conversion uses a reference voltage of `5.0 V`. These values are constants near the beginning of the Python source and must match the target hardware.

### TCA9539 output assignment

All TCA9539 pins on ports 0 and 1 are configured as outputs. The following names and pin assignments are defined in the software:

| TCA9539 pin | Software signal | Function |
|---|---|---|
| P0.0 | `FET1_CTL` | Dosimeter position 1 control |
| P0.1 | `FET1_R1` | Position 1, R1 selection |
| P0.2 | `FET1_R2` | Position 1, R2 selection |
| P0.3 | `FET2_CTL` | Dosimeter position 2 control |
| P0.4 | `FET2_R1` | Position 2, R1 selection |
| P0.5 | `FET2_R2` | Position 2, R2 selection |
| P0.6 | `FET3_CTL` | Dosimeter position 3 control |
| P0.7 | `FET3_R1` | Position 3, R1 selection |
| P1.0 | `FET3_R2` | Position 3, R2 selection |
| P1.1 | `FET4_CTL` | Dosimeter position 4 control |
| P1.2 | `FET4_R1` | Position 4, R1 selection |
| P1.3 | `FET4_R2` | Position 4, R2 selection |
| P1.4 | `FET5_CTL` | Dosimeter position 5 control |
| P1.5 | `FET5_R1` | Position 5, R1 selection |
| P1.6 | `FET5_R2` | Position 5, R2 selection |
| P1.7 | Unused | Always written low by this application |

The resulting output masks are:

| State | Port 0 | Port 1 | Meaning |
|---|---:|---:|---|
| R1 enabled | `0xDB` | `0x36` | Assert all control signals and R1 selections |
| R2 enabled | `0x6D` | `0x5B` | Assert all control signals and R2 selections |
| All disabled | `0x00` | `0x00` | Deassert every output |

The software writes the TCA9539 output registers before configuring the pins as outputs. This establishes a known low state before the direction change.

### ADS7138 access

The ADS7138 is accessed with the following protocol values:

| Item | Value |
|---|---:|
| Register read opcode | `0x10` |
| Register write opcode | `0x08` |
| Channel-selection register | `0x11` |
| Selected hardware channels | `2` through `6` |
| ADC resolution used by software | 12 bits |
| Reference voltage | `5.0 V` |

For each selected channel, the program reads two bytes and discards the least-significant four bits:

```text
raw_adc = ((byte_0 << 8) | byte_1) >> 4
voltage = raw_adc * VREF / 4095
```

ADS7138 hardware channels 2–6 are written to the output CSV as logical channels 1–5. This one-position offset is intentional in the supplied software.

## Acquisition Sequence

One invocation performs a single complete acquisition cycle:

1. Initialize the primary and backup logger state.
2. Open Linux I²C bus 1 for the ADS7138 and bus 7 for the TCA9539.
3. Initialize both TCA9539 output registers to zero.
4. Configure both TCA9539 ports as outputs.
5. Wait 50 ms.
6. Enable the R1 sensor bank.
7. Wait 200 ms for the selected bank to settle.
8. Select, verify, read, and log ADS7138 hardware channels 2–6.
9. Wait 10 ms after each successful channel sample.
10. Disable all sensors and wait 500 ms before switching banks.
11. Enable the R2 sensor bank and wait 200 ms.
12. Repeat the five-channel ADC scan for R2.
13. Disable all sensors.
14. Close both measurement logs and exit.

This program does **not** contain an infinite loop. Continuous or periodic collection should be provided by a service manager or scheduler; see [Running Periodically](#running-periodically).

## Requirements

### Software

- Linux with I²C device support enabled
- Python 3
- `smbus2`
- Permission to access `/dev/i2c-1` and `/dev/i2c-7`

Install the Python dependency:

```bash
python3 -m pip install smbus2
```

On Debian- or Raspberry Pi OS-based systems, the I²C utilities are also useful for diagnostics:

```bash
sudo apt update
sudo apt install i2c-tools
```

### Hardware assumptions

- The ADS7138 is reachable at address `0x10` on bus 1.
- The TCA9539 is reachable at address `0x74` on bus 7.
- The ADS7138 analog supply/reference and input range are compatible with `VREF = 5.0`.
- The TCA9539 outputs are wired to the VT01 routing/control circuit as shown above.
- External circuitry ensures that enabling the defined FET control and R1/R2 selection combinations is electrically safe.

Confirm the devices before running:

```bash
i2cdetect -y 1
i2cdetect -y 7
```

The first scan should show a device at `10`; the second should show one at `74`.

## Configuration

The application is configured through constants in the source file:

```python
PRIMARY_LOG_DIR = "radfet_logs"
BACKUP_LOG_DIR = "backup_logs"
ADS_BUS = 1
TCA_BUS = 7
TCA9539_ADDR = 0x74
ADS_ADDR = 0x10
VREF = 5.0
```

Relative log directories are resolved from the process's current working directory, not necessarily the directory containing the script. The error log is different: `melagen_error_log.csv` is always stored beside the Python script.

Before deployment, verify:

- bus numbers against the Linux `/dev/i2c-*` devices;
- device addresses against the address pins fitted to the board;
- `VREF` against the actual ADC reference or supply used for conversion scaling;
- the TCA9539 pin mapping and active-high behavior against the schematic;
- the working directory used by a scheduler or service, since it determines measurement-log placement.

## Running the Program

Assuming the source is named `radfet_logger.py`:

```bash
python3 radfet_logger.py
```

It can also be made executable:

```bash
chmod +x radfet_logger.py
./radfet_logger.py
```

A successful run prints the configuration, selected bank, logical channel, raw ADC code, converted voltage, and final log paths. The process returns exit status `0` after its normal top-level flow, including cases where an I²C exception was caught and recorded. Operational monitoring should therefore inspect the error CSV as well as the process exit status.

## Output Files

### Measurement logs

The software creates two copies of each daily log:

```text
radfet_logs/radfet_YYYY-MM-DD.csv
backup_logs/radfet_YYYY-MM-DD.csv
```

Both files use this schema:

| Column | Description | Example |
|---|---|---|
| `timestamp` | Local system time in ISO 8601 form | `2026-07-22T14:30:15.123456` |
| `sensor_group` | Selected sensor bank | `R1` or `R2` |
| `channel` | Logical channel number | `1` through `5` |
| `raw_adc` | 12-bit ADC code | `2048` |
| `voltage` | Voltage calculated from `VREF` | `2.5006105006105006` |

Example:

```csv
timestamp,sensor_group,channel,raw_adc,voltage
2026-07-22T14:30:15.123456,R1,1,2048,2.5006105006105006
2026-07-22T14:30:15.145678,R1,2,2011,2.4554334554334554
```

Existing files are opened in append mode. Headers are written only when a file is new or empty. A long-running process changes to a new filename after midnight on the host's local clock.

The backup directory is redundant only at the directory level. If both directories are on the same physical storage device, it does not protect against device failure; mount `backup_logs` on separate storage if that protection is required.

### Error log

Errors are appended to:

```text
melagen_error_log.csv
```

Its columns are:

```csv
time,error code,error message
```

The timestamp has one-second precision. If the error log itself cannot be written, the application prints an `[ERROR LOG FAILURE]` message to standard output instead of recursively attempting another log write.

Common error codes include:

| Error code | Meaning |
|---|---|
| `MAIN_I2C_ERROR` | An I²C bus could not be opened or the top-level bus operation failed |
| `TCA_CONFIG_ERROR` | One or more TCA9539 initialization writes failed |
| `TCA_CONFIG_FATAL` | Acquisition was skipped because TCA9539 setup was unsuccessful |
| `TCA_WRITE_ERROR` | A TCA9539 register write failed |
| `IO_EXPANDER_ERROR` | One or both requested output-port writes failed |
| `ADS_WRITE_ERROR` | The ADS7138 channel-selection write failed |
| `ADS_REGISTER_READ_ERROR` | The channel-selection register could not be read |
| `CHANNEL_VERIFY_ERROR` | The channel read back did not match the selected channel |
| `ADC_READ_ERROR` | The two-byte conversion read failed |
| `CHANNEL_READ_ERROR` | An unexpected failure occurred while processing a channel |
| `LOG_DIR_ERROR` | A measurement log directory could not be created or accessed |
| `LOG_OPEN_ERROR` | A daily CSV could not be opened |
| `LOG_WRITE_ERROR` | A row could not be written to one log destination |
| `ALL_LOGS_UNAVAILABLE` | Neither primary nor backup accepted a measurement row |

## Running Periodically

Because each execution performs one R1/R2 scan, a systemd timer or cron job can supply the collection interval. Ensure that scheduled runs cannot overlap, because the application does not implement inter-process locking and simultaneous processes would also manipulate the same sensor-selection outputs.

Example cron entry for one acquisition every minute:

```cron
* * * * * cd /opt/vt01-logger && /usr/bin/python3 radfet_logger.py >> acquisition_console.log 2>&1
```

For production use, a systemd service/timer is generally preferable because it provides controlled working directories, permissions, restart policy, and centralized console logs.

## Failure Behavior and Safety

- A failed ADC channel selection, verification, or conversion is logged and that channel is skipped.
- A failed R1 enable skips the R1 scan. The application still calls `disable_all()` before proceeding.
- A failed R2 enable skips the R2 scan. The application still calls `disable_all()` afterward.
- A TCA9539 configuration failure prevents both sensor-bank scans.
- If one measurement log fails, acquisition continues using the other log.
- If both measurement logs fail, the measurement is lost and `ALL_LOGS_UNAVAILABLE` is recorded when possible.
- The program makes best-effort calls to disable outputs, but it has no independent hardware interlock. A process kill, host crash, bus failure, or power interruption can prevent the final write from reaching the TCA9539. Hardware should default to a safe state through pull resistors or other external circuitry.

## Troubleshooting

### I²C bus cannot be opened

- Confirm `/dev/i2c-1` and `/dev/i2c-7` exist.
- Enable I²C in the platform configuration.
- Check that the running account belongs to the appropriate I²C-access group, or test with suitable privileges.
- Confirm that bus 7 is not a mistaken board-specific bus number.

### Device is missing from `i2cdetect`

- Check power, ground, SDA, and SCL continuity.
- Check pull-up resistors and logic voltage compatibility.
- Verify the address-selection pins.
- Confirm that the device is connected to the expected bus.

### `CHANNEL_VERIFY_ERROR`

- Confirm the ADS7138 command opcodes and register address against the exact device revision and operating mode.
- Check for I²C signal integrity problems.
- Ensure no other process is changing the ADC configuration concurrently.

### Voltages are consistently incorrect

- Measure the real ADC reference/supply and update `VREF`.
- Confirm that inputs stay within the ADC input range.
- Check whether analog gain, dividers, offsets, or calibration factors must be incorporated. The supplied software performs only ideal linear code-to-voltage conversion and does not calculate absorbed dose.

### Logs appear in an unexpected location

`radfet_logs` and `backup_logs` are relative to the working directory used to launch the program. Set an explicit working directory in cron/systemd or replace the constants with absolute paths.

### No measurement row for a channel

Review `melagen_error_log.csv`. The implementation intentionally skips a channel after a failed selection write, channel verification, or ADC conversion instead of writing a placeholder row.


## Data Interpretation

For each ADC sample:

```text
voltage = raw_adc × 5.0 / 4095
```

Therefore:

- raw code `0` corresponds to `0 V`;
- raw code `4095` corresponds to `5.0 V`;
- one ideal least-significant bit is approximately `1.221 mV`.

These values describe the software conversion only. Turning a VT01 electrical reading into a dose value requires the applicable device characterization, readout method, calibration data, and environmental corrections.

