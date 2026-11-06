# 繼電器測試 - UIFlow 1 (MicroPython)
# M5Stack Basic + Servo2 (PCA9685, I2C 0x40)，繼電器訊號接 Servo2 的 15 號通道
# 按鍵：A 吸合(ON)  B 放開(OFF)  C 自動循環(2 秒開、2 秒關，共 3 次)

from m5stack import *
from m5ui import *
from uiflow import *
from machine import I2C, Pin
import time

setScreenColor(0x000000)

RELAY_CH = 15
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

# 掃描 I2C，自動找 Servo2 位址（找不到就顯示出來，不當機）
time.sleep_ms(500)
found = []
for _ in range(5):
    try:
        found = i2c.scan()
    except Exception:
        found = []
    if found:
        break
    time.sleep_ms(200)
cand = [a for a in found if 0x40 <= a <= 0x6F]
if cand:
    PCA_ADDR = cand[0]
else:
    lcd.clear(0x000000)
    lcd.font(lcd.FONT_DejaVu18)
    lcd.setTextColor(0xFF0000, 0x000000)
    lcd.print('No Servo2 found', 10, 60)
    lcd.setTextColor(0xFFFFFF, 0x000000)
    lcd.print('scan: ' + str([hex(a) for a in found]), 10, 100)
    lcd.print('unplug relay from PortA', 10, 140)
    while True:
        time.sleep(1)

def pca_write(reg, data):
    i2c.writeto(PCA_ADDR, bytes([reg] + list(data)))

def set_channel(ch, on, off):
    pca_write(0x06 + 4 * ch, [on & 0xFF, on >> 8, off & 0xFF, off >> 8])

def relay(on):
    if on:
        set_channel(RELAY_CH, 0x1000, 0)   # 恆為高電位
    else:
        set_channel(RELAY_CH, 0, 0x1000)   # 恆為低電位

def show(line1, line2, color):
    lcd.clear(0x000000)
    lcd.font(lcd.FONT_DejaVu18)
    lcd.setTextColor(0xFFFFFF, 0x000000)
    lcd.print('Relay CH%d test' % RELAY_CH, 10, 20)
    lcd.setTextColor(color, 0x000000)
    lcd.font(lcd.FONT_DejaVu24)
    lcd.print(line1, 10, 80)
    lcd.font(lcd.FONT_DejaVu18)
    lcd.setTextColor(0xAAAAAA, 0x000000)
    lcd.print(line2, 10, 140)
    lcd.print('A:ON  B:OFF  C:auto', 10, 210)

# 初始化 PCA9685（50Hz），之後繼電器預設放開
pca_write(0x00, [0x10])
pca_write(0xFE, [121])
pca_write(0x00, [0x20])
time.sleep_ms(1)
relay(False)
show('OFF', 'ready', 0xFF0000)

while True:
    if btnA.wasPressed():
        relay(True)
        show('ON', 'relay closed', 0x00FF00)
    if btnB.wasPressed():
        relay(False)
        show('OFF', 'relay open', 0xFF0000)
    if btnC.wasPressed():
        for i in range(3):
            relay(True)
            show('ON', 'auto %d/3' % (i + 1), 0x00FF00)
            time.sleep(2)
            relay(False)
            show('OFF', 'auto %d/3' % (i + 1), 0xFF0000)
            time.sleep(2)
        show('OFF', 'done', 0xFF0000)
    time.sleep_ms(20)
