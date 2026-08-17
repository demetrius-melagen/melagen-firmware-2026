#melagen rs422 bridge implementation


import binascii
import time
from typing import Union

#aegis encoding function
def encoder(data: Union[bytes, str, None] = None, deviceID: int = 0, messageID:
int = 0) -> bytes:
    '''
    Encode a Message. If 'data' is of type str, it will be converted to a byte string
    and encoded using UTF-8; otherwise, 'data' must be supplied as a byte string.
    Returns a formatted Message as a byte string.
    '''
    length = 0
    if data:
        # If the data happens to be a string, convert it to a bytearray.
        if isinstance(data, str):
            data = bytearray(data, encoding='utf-8')
            length = len(data)
            # The size of the message data can be no larger than the number of
            # bytes allocated, which is two bytes.
            if length > 0xfff1:
                raise ValueError(f'Data packet size of {length:#6x} bytes exceeds limit of 0xfff1 bytes')
    # Bytes 1 - 2: Start of Message flag
    msg = bytearray(b'\xa5\x3D')
    print(msg)

    # Bytes 3 - 4: Length of Message
    msg.extend(length.to_bytes(2, 'big'))
    # Bytes 5 - 8: Message Timestamp (seconds since epoch)
    msg.extend(int(time.time()).to_bytes(4, 'big'))
    # Bytes 9 - 10: Device ID
    msg.extend(deviceID.to_bytes(2, 'big'))
    # Bytes 11 - 12: Message ID
    msg.extend(messageID.to_bytes(2, 'big'))
    # Bytes 12 - (length + 12): Add the data to the message.
    if length > 0:
        msg.extend(data)
    # The next two bytes are reserved.
    msg.extend(b'\x00\x00')
    # Add the checksum to the message
    checksum = binascii.crc_hqx(msg, 0)
    msg.extend(checksum.to_bytes(2, 'big'))
    # print(msg)
    return bytes(msg)

def chunks(a, chunk_size):
    for i in range(0, len(a), chunk_size):
        yield a[i : i + chunk_size]

def csv_serial_send(csv, ser):
    with open(csv, 'r', encoding='utf-8') as file:
        text = file.read()
    
    # joining with space content of text
    # text = ''.join([i for i in text])  

    # replacing ',' by space
    # text = text.replace(",", " ")  
    encoded_messages = []

    for chunk in chunks(text, 0xfff1):
        encoding = encoder(chunk)
        encoded_messages.append(encoding)
        # print(encoding)
        ser.write(encoding)
        time.sleep(1)
    return encoded_messages



# Source - https://stackoverflow.com/q/78199772
# Posted by Ondřej Hojný, modified by community. See post 'Timeline' for change history
# Retrieved 2026-08-15, License - CC BY-SA 4.0

import serial
import time
ser = serial.Serial(
    port='/dev/ttyTHS1',
    baudrate = 115200,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    bytesize=serial.EIGHTBITS,
    timeout=1
)
# ser = 0
# print(ser.name)
# print(ser.baudrate)
try:
    # message = 'GETINFO'
    # message = message.encode('ascii')
    messages = csv_serial_send('flower_bot_training_template.csv', ser)
    # for message in messages:
    #     print(message)
    #     ser.write(message)
    #     # data = ser.read(0xfff1)
    #     # print(f"read bytes in buffer:{data}")
    #     time.sleep(1)
    # # #time.sleep(1)

    # read = ser.read(100)
    # print(read)
    # if ser.in_waiting > 0:
    #     available = ser.read(ser.in_waiting)
    # ser.close()

except Exception as e:
    print(f"{e}")



