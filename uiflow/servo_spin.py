# 單顆 MG996R 360 連續旋轉測試 - UIFlow 1 (MicroPython)
# M5Stack Basic + Module13.2 Servo2 (PCA9685, I2C 0x40)，馬達接在接口 0
#
# 按鍵：A：一直轉   B：停止
# (螢幕用英文顯示)

from m5stack import *
from m5ui import *
from uiflow import *
from machine import I2C, Pin
import time

setScreenColor(0x000000)

# ================= 設定值 =================
SERVO_CH = 0        # 馬達接在 Servo2 的接口 0
SPIN_US  = 1700     # 轉動脈衝(us)：1500 附近是停止點，越遠越快；轉反了改成 1300

# ================= Servo2 (PCA9685) =================
PCA_ADDR = 0x40

def make_i2c():
    for build in (
        lambda: I2C(0, scl=Pin(22), sda=Pin(21), freq=400000),
        lambda: I2C(scl=Pin(22), sda=Pin(21), freq=400000),
        lambda: I2C(0, scl=Pin(22), sda=Pin(21), speed=400000),
        lambda: I2C(sda=21, scl=22, freq=400000),
    ):
        try:
            return build()
        except Exception:
            pass
    raise OSError('I2C init failed')

i2c = make_i2c()

def pca_write(reg, data):
    i2c.writeto(PCA_ADDR, bytes([reg] + list(data)))

def set_channel(ch, on, off):
    pca_write(0x06 + 4 * ch, [on & 0xFF, on >> 8, off & 0xFF, off >> 8])

def spin():
    set_channel(SERVO_CH, 0, SPIN_US * 4096 // 20000)

def stop():
    # 完全不送脈衝，馬達停住
    set_channel(SERVO_CH, 0, 0x1000)

pca_write(0x00, [0x10])   # 睡眠才能改頻率
pca_write(0xFE, [121])    # 50Hz
pca_write(0x00, [0x20])   # 喚醒、自動遞增
time.sleep_ms(1)
stop()

# ================= 螢幕 =================
def show(text, color):
    lcd.clear(0x000000)
    lcd.font(lcd.FONT_DejaVu24)
    lcd.setColor(color, 0x000000)
    lcd.print(text, 10, 80)
    lcd.font(lcd.FONT_DejaVu18)
    lcd.setColor(0xFFFFFF, 0x000000)
    lcd.print('A: spin   B: stop', 10, 200)

show('STOPPED', 0xFF0000)

# ================= 主迴圈 =================
while True:
    if btnA.wasPressed():
        spin()
        show('SPINNING', 0x00FF00)
    if btnB.wasPressed():
        stop()
        show('STOPPED', 0xFF0000)
    time.sleep_ms(20)
