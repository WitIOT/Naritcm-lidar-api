from pymodbus.client import ModbusSerialClient
from typing import Tuple

class RS485Modbus:
    def __init__(self, port:str="/dev/ttyACM0", baudrate:int=9600, parity:str='N', bytesize:int=8, stopbits:int=1, unit:int=1, timeout:float=1.0):
        self.client = ModbusSerialClient(
            method="rtu",
            port=port,
            baudrate=baudrate,
            parity=parity,
            bytesize=bytesize,
            stopbits=stopbits,
            timeout=timeout
        )
        self.unit = unit

    def connect(self)->bool:
        if self.client.connected:  # pymodbus keeps state
            return True
        return self.client.connect()

    def close(self):
        try:
            self.client.close()
        except Exception:
            pass

    def read_holding(self, address:int=1, count:int=2) -> Tuple[int, ...]:
        """
        อ่าน Holding Registers (Function Code 03)
        ตามโจทย์: -t 4 (บางเครื่องมือระบุ 4=Holding) -r 1 -c 2
        """
        if not self.connect():
            raise RuntimeError("Modbus not connected")
        rr = self.client.read_holding_registers(address=address, count=count, slave=self.unit)
        if rr.isError():
            raise RuntimeError(f"Modbus error: {rr}")
        return tuple(rr.registers)
