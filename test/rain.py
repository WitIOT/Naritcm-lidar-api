import serial
import time

# เปิด serial port
ser = serial.Serial(
    port='/dev/ttyRG15',
    baudrate=9600,
    bytesize=serial.EIGHTBITS,
    parity=serial.PARITY_NONE,
    stopbits=serial.STOPBITS_ONE,
    timeout=2
)

print(f"เปิด port: {ser.name}")
print("รอข้อมูลจาก RG-15...\n")

try:
    while True:
        # ส่งคำสั่ง R เพื่อขอข้อมูล
        ser.write(b'R\n')
        time.sleep(0.5)
        
        # อ่านข้อมูลที่ได้รับ
        if ser.in_waiting > 0:
            data = ser.readline().decode('utf-8', errors='ignore').strip()
            if data:
                print(f"[{time.strftime('%H:%M:%S')}] {data}")
        
        time.sleep(2)

except KeyboardInterrupt:
    print("\nหยุดการทำงาน")
    ser.close()
