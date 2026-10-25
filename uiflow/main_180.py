# 自動摺衣板 - UIFlow 1 (MicroPython) 【180 度角度版】
# M5Stack Basic + Module13.2 Servo2 (PCA9685, I2C 0x40) + MG996R 180 度版 x3
#
# 用法：UIFlow 選 Python 模式，整份貼上即可。
# 按鍵：
#   待機中   A：開始摺衣   C：進入測試模式
#   任何時候 B：緊急停止
#   測試模式 A：換馬達   C：轉一次   長按C：離開
# (UIFlow 1 內建字型不支援中文，螢幕用英文顯示)

from m5stack import *
from m5ui import *
from uiflow import *
from machine import I2C, Pin
import time

setScreenColor(0x000000)

# ================= 設定值（微調都改這裡） =================
SERVO_CH   = [0, 1, 2]            # 三顆馬達接 Servo2 的接口
ANGLE_HOME = [0, 0, 0]            # 放下（原位）角度
ANGLE_FOLD = [180, 180, 180]      # 翻起角度，轉反了就把 HOME/FOLD 對調
MIN_US     = 500                  # 0 度的脈衝(us)，馬達抖動或卡住就往內縮（例如 600）
MAX_US     = 2500                 # 180 度的脈衝(us)（例如 2400）
STEP_DEG   = 3                    # 每一小步轉幾度，越小越慢越平順
STEP_MS    = 15                   # 每一小步間隔(ms)；速度約 STEP_DEG/STEP_MS*1000 度/秒
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
    i2c.writeto(PCA_ADDR, bytes([reg] + list(data)))

def set_channel(ch, on, off):
    pca_write(0x06 + 4 * ch, [on & 0xFF, on >> 8, off & 0xFF, off >> 8])

cur = [None, None, None]   # 每顆馬達目前角度，None = 還沒送過脈衝（位置未知）

def servo_angle(m, deg):
    us = MIN_US + (MAX_US - MIN_US) * deg // 180
    set_channel(SERVO_CH[m], 0, us * 4096 // 20000)
    cur[m] = deg

def servo_off(m):
    # 完全不送脈衝，馬達放鬆（位置不再被鎖住）
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
    show('TEST MODE', MOTOR_NAME[m] + '  A:next C:run', 'hold C:exit', 0x00FFFF)

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

kA = Key(btnA)
kC = Key(btnC)

# ================= 動作 =================
IDLE, TEST, STOPPED = 0, 1, 2
mode = IDLE
test_motor = 0

def emergency_stop():
    global mode
    stop_all()
    cur[0] = cur[1] = cur[2] = None   # 馬達放鬆後位置未知
    mode = STOPPED
    show('E-STOP', 'Put boards back', 'Press A to restart', 0xFF0000)
    print('緊急停止：請把摺板扶回原位，再按 A 重新開始')

def b_stop_check():
    # B 鍵被按下就急停，回傳 True
    if btnB.isPressed():
        emergency_stop()
        while btnB.isPressed():
            time.sleep_ms(10)
        return True
    return False

def wait_or_stop(ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        if b_stop_check():
            return False
        time.sleep_ms(1)
    return True

def move_to(m, target):
    # 一小步一小步轉到目標角度，每一步都檢查 B 鍵；被急停回傳 False
    while cur[m] != target:
        if b_stop_check():
            return False
        d = target - cur[m]
        if d > STEP_DEG:
            d = STEP_DEG
        elif d < -STEP_DEG:
            d = -STEP_DEG
        servo_angle(m, cur[m] + d)
        time.sleep_ms(STEP_MS)
    return True

def run_motor(m):
    name = MOTOR_NAME[m]
    print(MOTOR_ZH[m], '翻起到', ANGLE_FOLD[m], '度')
    show(name + ' RUN', 'Folding...', 'B: emergency stop', 0xFFFF00)
    if cur[m] is None:
        # 開機或急停後位置未知，板子應該已扶回原位：先到原位，再慢慢翻起
        servo_angle(m, ANGLE_HOME[m])
        if not wait_or_stop(500):
            return False
    if not move_to(m, ANGLE_FOLD[m]):
        return False
    if not wait_or_stop(HOLD_MS):
        return False

    print(MOTOR_ZH[m], '放下到', ANGLE_HOME[m], '度')
    show(name + ' RUN', 'Returning...', 'B: emergency stop', 0xFFFF00)
    return move_to(m, ANGLE_HOME[m])

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
print('Servo2 正常，待機中（180 度版）')
show('FOLD BOARD', 'ver 3: 180 deg', '', 0xFFFFFF)   # 開機畫面，確認板子上是新版
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
        if ea == 'click':
            test_motor = (test_motor + 1) % 3
            show_test(test_motor)
        elif ec == 'hold':
            mode = IDLE
            show_idle()
        elif ec == 'click':
            if run_motor(test_motor):
                show_test(test_motor)

    time.sleep_ms(5)
