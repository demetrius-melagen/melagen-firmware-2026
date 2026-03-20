from smbus2 import SMBus

bus = SMBus(7)  # I2C bus 7
addr = 0x74     # TCA9539

# Configure all pins as outputs
bus.write_byte_data(addr, 0x06, 0x00)
bus.write_byte_data(addr, 0x07, 0x00)

# Set some outputs high
bus.write_byte_data(addr, 0x02, 0xFF)
bus.write_byte_data(addr, 0x03, 0xFF)

