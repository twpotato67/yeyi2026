# 自動摺衣板 - UIFlow 1 (MicroPython)
# M5Stack Basic + Module13.2 Servo2 (PCA9685, I2C 0x40) + MG996R 360 x3
#
# 用法：UIFlow 選 Python 模式，整份貼上即可。
# 按鍵：
#   待機中   A：開始摺衣   C：進入測試模式
#   任何時候 B：緊急停止
#   測試模式 A：換馬達   C：轉一次   長按A：停止點校正   長按C：離開
#   停止點校正 A：-5us   C：+5us   B：離開
# (UIFlow 1 內建字型不支援中文，螢幕用英文顯示)

from m5stack import *
from m5ui import *
from uiflow import *
from machine import I2C, Pin
import time

setScreenColor(0x000000)

# ================= 設定值（微調都改這裡） =================
SERVO_CH   = [0, 1, 2]            # 三顆馬達接 Servo2 的接口
NEUTRAL_US = [1500, 1500, 1500]   # 停止點(us)，用校正模式找
SPEED_US   = [500, 500, 500]      # 與停止點差多少 us，越大越快
FOLD_DIR   = [1, 1, 1]            # 翻起方向 +1/-1，轉反了就改
FOLD_MS    = [297, 297, 297]      # 翻起 180 度要轉多久(ms)
RETURN_MS  = [297, 297, 297]      # 放下要轉多久(ms)
HOLD_MS    = 300                  # 翻起後頂端停留
PAUSE_MS   = 500                  # 每步之間停頓
STEPS      = [0, 1, 0, 2]         # 摺衣順序
HOLD_PRESS_MS = 800               # 長按判定時間

# ================= Servo2 (PCA9685) =================
PCA_ADDR = 0x40

def make_i2c():
    # 不同版本的 UIFlow 1 韌體 I2C 參數不同，依序嘗試
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
    # 用最通用的 writeto：第一個位元組是暫存器位址，後面接資料
    i2c.writeto(PCA_ADDR, bytes([reg] + list(data)))

def set_channel(ch, on, off):
    pca_write(0x06 + 4 * ch, [on & 0xFF, on >> 8, off & 0xFF, off >> 8])

def servo_pulse(m, us):
    set_channel(SERVO_CH[m], 0, us * 4096 // 20000)

def servo_off(m):
    # 完全不送脈衝，馬達停住，不受停止點誤差影響
    set_channel(SERVO_CH[m], 0, 0x1000)

def stop_all():
    for i in range(3):
        servo_off(i)

def pca_begin():
    pca_write(0x00, [0x10])   # 睡眠才能改頻率
    pca_write(0xFE, [121])    # 50Hz
    pca_write(0x00, [0x20])   # 喚醒、自動遞增
    time.sleep_ms(1)
    stop_all()

# ================= 螢幕 =================
def show(title, l1='', l2='', color=0xFFFFFF, footer='B: stop'):
    lcd.clear(0x000000)
    lcd.font(lcd.FONT_DejaVu24)
    lcd.setColor(color, 0x000000)
    lcd.print(title, 10, 20)
    lcd.font(lcd.FONT_DejaVu18)
    lcd.setColor(0xFFFFFF, 0x000000)
    lcd.print(l1, 10, 100)
    lcd.print(l2, 10, 140)
    if footer:
        lcd.print(footer, 10, 190)   # B 鍵永遠是緊急停止，每個畫面都提醒

MOTOR_NAME = ['Motor 1', 'Motor 2', 'Motor 3']   # 螢幕用（英文）
MOTOR_ZH   = ['馬達一', '馬達二', '馬達三']       # 輸出視窗用（中文）

def show_idle():
    show('READY', 'A: start folding', 'C: test mode')

def show_test(m):
    show('TEST MODE', MOTOR_NAME[m] + '  A:next C:run', 'hold A:calib hold C:exit', 0x00FFFF)

# ================= 按鍵（自行做按下/長按判斷） =================
class Key:
    def __init__(self, btn):
        self.btn = btn
        self.down = False
        self.t0 = 0
        self.held = False

    def update(self):
        # 回傳 None / 'click' / 'hold'
        pressed = self.btn.isPressed()
        ev = None
        if pressed and not self.down:
            self.down = True
            self.held = False
            self.t0 = time.ticks_ms()
        elif pressed and self.down:
            if not self.held and time.ticks_diff(time.ticks_ms(), self.t0) >= HOLD_PRESS_MS:
                self.held = True
                ev = 'hold'
        elif not pressed and self.down:
            self.down = False
            if not self.held:
                ev = 'click'
        return ev

    def fast_click(self):
        # 按下瞬間就觸發（用在急停、校正，不分長短按）
        pressed = self.btn.isPressed()
        ev = pressed and not self.down
        self.down = pressed
        return ev

kA = Key(btnA)
kB = Key(btnB)
kC = Key(btnC)

# ================= 動作 =================
IDLE, TEST, STOPPED = 0, 1, 2
mode = IDLE
test_motor = 0

def emergency_stop():
    global mode
    stop_all()
    mode = STOPPED
    show('E-STOP', 'Put boards back', 'Press A to restart', 0xFF0000)
    print('緊急停止：請把摺板扶回原位，再按 A 重新開始')

def wait_or_stop(ms):
    # 等待期間一直檢查 B 鍵，被急停回傳 False
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        if btnB.isPressed():
            emergency_stop()
            while btnB.isPressed():
                time.sleep_ms(10)
            kB.down = False
            return False
        time.sleep_ms(1)
    return True

def run_motor(m):
    name = MOTOR_NAME[m]
    print(MOTOR_ZH[m], '翻起', FOLD_MS[m], 'ms')
    show(name + ' RUN', 'Folding...', 'B: emergency stop', 0xFFFF00)
    servo_pulse(m, NEUTRAL_US[m] + FOLD_DIR[m] * SPEED_US[m])
    if not wait_or_stop(FOLD_MS[m]):
        return False
    servo_off(m)
    if not wait_or_stop(HOLD_MS):
        return False

    print(MOTOR_ZH[m], '放下', RETURN_MS[m], 'ms')
    show(name + ' RUN', 'Returning...', 'B: emergency stop', 0xFFFF00)
    servo_pulse(m, NEUTRAL_US[m] - FOLD_DIR[m] * SPEED_US[m])
    if not wait_or_stop(RETURN_MS[m]):
        return False
    servo_off(m)
    return True

def run_fold_sequence():
    global mode
    print('開始摺衣')
    for m in STEPS:
        if not run_motor(m):
            return
        if not wait_or_stop(PAUSE_MS):
            return
    mode = IDLE
    print('摺衣完成')
    show('DONE!', 'A: fold again', 'C: test mode', 0x00FF00)

def calibrate(m):
    # 送停止點脈衝，A/C 微調到馬達完全不動，B 離開
    us = NEUTRAL_US[m]
    while btnA.isPressed():
        time.sleep_ms(10)
    kA.down = False
    kC.down = False
    kB.down = False
    while True:
        servo_pulse(m, us)
        show(MOTOR_NAME[m] + ' CALIB', 'neutral: %d us' % us, 'A:-5 C:+5 B:exit', 0xFFA500)
        print(MOTOR_ZH[m], '停止點 =', us, 'us')
        while True:
            if kA.fast_click():
                us -= 5
                break
            if kC.fast_click():
                us += 5
                break
            if kB.fast_click():
                servo_off(m)
                NEUTRAL_US[m] = us   # 重開機會恢復，記得填回設定值
                show_test(m)
                return
            time.sleep_ms(5)

# ================= 主程式 =================
try:
    if PCA_ADDR not in i2c.scan():
        raise OSError('0x40 not found')
    pca_begin()
except Exception as e:
    show('NO SERVO2', 'Check module stack', 'then reboot', 0xFF0000)
    print('找不到 Servo2（0x40），請檢查模組有沒有疊好，再重開機。錯誤：', e)
    while True:
        time.sleep_ms(1000)
print('Servo2 正常，待機中（版本 2：每個畫面都有 B: stop）')
show('FOLD BOARD', 'ver 2: B-stop footer', '', 0xFFFFFF)   # 開機畫面，確認板子上是新版
time.sleep_ms(1500)
show_idle()

while True:
    # B 鍵：任何時候緊急停止
    if btnB.isPressed():
        emergency_stop()
        while btnB.isPressed():
            time.sleep_ms(10)
        kA.down = kC.down = False
        continue

    ea = kA.update()
    ec = kC.update()

    if mode == IDLE or mode == STOPPED:
        if ea == 'click':
            mode = IDLE
            run_fold_sequence()
        elif mode == IDLE and ec == 'click':
            mode = TEST
            show_test(test_motor)
    elif mode == TEST:
        if ea == 'hold':
            calibrate(test_motor)
        elif ea == 'click':
            test_motor = (test_motor + 1) % 3
            show_test(test_motor)
        elif ec == 'hold':
            mode = IDLE
            show_idle()
        elif ec == 'click':
            if run_motor(test_motor):
                show_test(test_motor)

    time.sleep_ms(5)
