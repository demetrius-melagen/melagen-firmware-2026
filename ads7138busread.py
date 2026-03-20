import smbus2

bus = smbus2.SMBus(7)  # Use the correct bus
addr = 0x10

# Write the register number to read
bus.write_byte(addr, 0x10)  # opcode for read
# Read two bytes
data = bus.read_i2c_block_data(addr, 0, 2)
print(data)

