# 智慧雨具守護者 - 摺衣機台 (M5Stack Basic + Module13.2 Servo2 + MG996R 180 度 x3) - UIFlow 1 (MicroPython)
#
# 流程：
#   顧客在網站 /customer 送件 -> 站點掃送洗 QR -> 網站送出 drop 事件(帶訂單編號)
#   -> M5 每 2 秒 poll 到 drop -> 回報「開始摺」(網頁顯示 機台正在摺衣中)
#   -> 先噴水 30 秒（水泵繼電器接 Servo2 的 6 號通道）
#   -> 馬達一(腳位0) 轉180度轉回 -> 馬達二(腳位3) 轉180度轉回 -> 馬達一 -> 馬達三(腳位4)
#   -> 回報「做完了」-> 網站訂單立刻變完成，顧客頁即時出現「摺衣做完了」
#
# 用法：UIFlow 選 Python 模式，改下面「設定」整份貼上。網站要先在 Coolify 設 DEVICE_KEY 並 Redeploy。
# 按鍵：A 立即更新網站   B 單獨摺衣(只摺，不沖水、不回報網站)   C 緊急停止(任何動作中都立刻停)
# 螢幕中文：FONT_CN 填字型檔路徑；空白或載入失敗就顯示英文

from m5stack import *
from m5ui import *
from uiflow import *
from machine import I2C, Pin

# ================= 繼電器強制關閉（放在最前面，開機越早關越好） =================
# 繼電器訊號腳：Core 的 Port B(黑)=26、Port C(藍)=17。不要用 Port A（會干擾 Servo2）
# 設成 None 就改用 Servo2 通道輸出（接線錯了會讓 Servo2 掃不到）
RELAY_GPIOS = [26, 17, 16, 3]   # 這三個腳位同時輸出，繼電器黃線接其中任何一個都會動（不要用 21/22，那是 Servo2 的 I2C）
# 水泵開的時候腳位要輸出什麼電位：水泵接 NO（常開）= 1；水泵接 NC（常閉，繼電器 ON 時水泵反而停）= 0
PUMP_ON_LEVEL = 1

def make_relay_pin(n):
    for build in (
        lambda: Pin(n, Pin.OUT),
        lambda: Pin(n, mode=Pin.OUT),
        lambda: Pin(n),
    ):
        try:
            p = build()
            p.value(1 - PUMP_ON_LEVEL)   # 一開始就是水泵關的電位
            return p
        except Exception:
            pass
    raise OSError('relay pin init failed')

relay_pins = [make_relay_pin(n) for n in RELAY_GPIOS]   # 立刻輸出低電位 = 繼電器關
import wifiCfg
import urequests
import time

setScreenColor(0x000000)

# ================= 設定 =================
WIFI_SSID  = 'potato wifi'              # WiFi 名稱（板子已自動連上時不會用到）
WIFI_PASS  = '你的WiFi密碼'            # WiFi 密碼
SERVER     = 'https://smartraingearguar.stu.techedu.cc'
DEVICE_KEY = '你的DEVICE_KEY'         # 要和網站環境變數 DEVICE_KEY 相同
POLL_MS    = 2000
FONT_CN    = ''                    # 中文字型，例如 '/flash/res/font/cn.fon'

SERVO_CH   = [0, 1, 2]             # 馬達一=腳位0、馬達二=腳位1、馬達三=腳位2
ANGLE_HOME = [0, 180, 0]            # 放下角度；轉反了就把 HOME/FOLD 對調
ANGLE_FOLD = [180, 0, 180]      # 翻起角度
MIN_US     = 500                   # 0 度脈衝(us)；馬達抖動或卡住就往內縮（例如 600）
MAX_US     = 2500                  # 180 度脈衝(us)（例如 2400）
STEP_DEG   = 3                     # 每一小步幾度，越小越慢越平順
STEP_MS    = 20                    # 每一小步間隔(ms)
HOLD_MS    = 300                   # 翻起後頂端停留
PAUSE_MS   = 500                   # 每顆馬達之間停頓
STEPS      = [0, 1, 0, 2]          # 摺衣順序：馬達一、二、一、三
PCA_ADDR   = 0x40
RELAY_CH   = 7                     # RELAY_GPIOS 是空的時，繼電器接在 Servo2 的幾號通道
USE_PUMP   = True                  # True = 自動噴水；False = 跳過噴水（水泵用開關手動）
PUMP_SECONDS = 15                  # 先噴水幾秒，噴完才開始摺
SPRAY_MOTOR_CH = 3                 # 噴水時一起轉的 360 度馬達（Servo2 腳位 3）；None = 不用
SPRAY_MOTOR_US = 1700              # 轉速/方向：>1500 一個方向，<1500 反方向（1500 附近不動；離 1500 越遠越快）

# ================= Servo2 (PCA9685) =================
def make_i2c():
    # 100kHz 比 400kHz 抗干擾（水泵/馬達電源雜訊會讓高速 I2C 掃不到）
    for build in (
        lambda: I2C(0, scl=Pin(22), sda=Pin(21), freq=100000),
        lambda: I2C(scl=Pin(22), sda=Pin(21), freq=100000),
        lambda: I2C(0, scl=Pin(22), sda=Pin(21), speed=100000),
        lambda: I2C(sda=21, scl=22, freq=100000),
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

cur = [None, None, None]           # 每顆馬達目前角度，None = 位置未知

def servo_angle(m, deg):
    us = MIN_US + (MAX_US - MIN_US) * deg // 180
    set_channel(SERVO_CH[m], 0, us * 4096 // 20000)
    cur[m] = deg

def pump(on):
    if relay_pins:
        for rp in relay_pins:
            rp.value(PUMP_ON_LEVEL if on else 1 - PUMP_ON_LEVEL)   # Grove 腳位
    elif on:
        set_channel(RELAY_CH, 0x1000, 0)      # Servo2 通道一直輸出高電位
    else:
        set_channel(RELAY_CH, 0, 0x1000)      # Servo2 通道一直輸出低電位

def spray_motor(on):
    if SPRAY_MOTOR_CH is None:
        return
    if on:
        set_channel(SPRAY_MOTOR_CH, 0, SPRAY_MOTOR_US * 4096 // 20000)
    else:
        set_channel(SPRAY_MOTOR_CH, 0, 0x1000)   # 不送脈衝 = 停

def stop_all():
    pump(False)                               # 急停時水泵一定要關
    spray_motor(False)                        # 噴水馬達也停
    for m in range(3):
        set_channel(SERVO_CH[m], 0, 0x1000)   # 不送脈衝，馬達放鬆
        cur[m] = None

def pca_begin():
    pca_write(0x00, [0x10])
    pca_write(0xFE, [121])                    # 50Hz
    pca_write(0x00, [0x20])
    time.sleep_ms(1)
    stop_all()

# ================= 螢幕 =================
def _cn_ok():
    if not FONT_CN:
        return False
    try:
        lcd.font(FONT_CN)
        return True
    except Exception:
        return False

CN = _cn_ok()

def T(zh, en):
    return zh if CN else en

def show(title, l1='', l2='', color=0xFFFFFF):
    lcd.clear(0x000000)
    lcd.font(FONT_CN if CN else lcd.FONT_DejaVu24)
    lcd.setColor(color, 0x000000)
    lcd.print(title, 10, 20)
    if not CN:
        lcd.font(lcd.FONT_DejaVu18)
    lcd.setColor(0xFFFFFF, 0x000000)
    lcd.print(l1, 10, 100)
    lcd.print(l2, 10, 140)
    lcd.print(T('B：緊急停止', 'B: stop'), 10, 190)

NAMES_ZH = ['馬達一', '馬達二', '馬達三']
NAMES_EN = ['Motor 1', 'Motor 2', 'Motor 3']

def mname(m):
    return NAMES_ZH[m] if CN else NAMES_EN[m]

# ================= 網路 =================
HEAD = {'X-Device-Key': DEVICE_KEY, 'Content-Type': 'application/json'}

def wait_wifi(ms):
    t = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t) < ms:
        if wifiCfg.wlan_sta.isconnected():
            return True
        time.sleep_ms(100)
    return False

def wifi_diag():
    # 連不上時，把原因顯示在螢幕：附近有沒有看到這個 WiFi、狀態碼
    try:
        wifiCfg.wlan_sta.active(True)
        found = [x[0].decode() if isinstance(x[0], bytes) else x[0] for x in wifiCfg.wlan_sta.scan()]
    except Exception as e:
        found = []
        print('scan failed:', e)
    print('WiFi scan:', found)
    seen = 'SSID found' if WIFI_SSID in found else 'SSID NOT seen'
    show(T('連不上WiFi', 'NO WIFI'), seen, 'n=%d st=%s' % (len(found), wifiCfg.wlan_sta.status()), 0xFF0000)

def connect():
    # UIFlow 的 WiFi 模式開機時通常已連線：先等 5 秒；沒連上才用 WIFI_SSID/WIFI_PASS 主動連
    show(T('連線中', 'WiFi...'), WIFI_SSID[:20])
    if wait_wifi(20000):
        print('WiFi OK:', wifiCfg.wlan_sta.ifconfig())
        return True
    if WIFI_SSID:
        # 不用 wifiCfg.doConnect：它會一直卡住不回來。直接用 MicroPython 的 STA，自己限時等。
        sta = wifiCfg.wlan_sta
        try:
            sta.active(True)
            try:
                sta.disconnect()
            except Exception:
                pass
            sta.connect(WIFI_SSID, WIFI_PASS)
        except Exception as e:
            print('connect failed:', e)
        if wait_wifi(20000):
            print('WiFi OK:', sta.ifconfig())
            return True
    return False

def api_get(path):
    r = urequests.get(SERVER + path, headers=HEAD)
    try:
        if r.status_code != 200:
            raise OSError('HTTP %d' % r.status_code)
        return r.json()
    finally:
        r.close()

def api_post(path, body):
    r = urequests.post(SERVER + path, headers=HEAD, data=body)
    try:
        return r.status_code == 200
    finally:
        r.close()

def report_fold(order_id, state):
    # 回報失敗(網路不穩)重試 3 次，「做完了」一定要送到
    for _ in range(3):
        try:
            if api_post('/api/device/fold', '{"order_id":%d,"state":"%s"}' % (order_id, state)):
                return True
        except Exception as e:
            print('report failed:', e)
        time.sleep_ms(500)
    return False

# ================= 動作（C 鍵隨時急停） =================
def c_stop_check():
    if btnC.isPressed():
        stop_all()
        while btnC.isPressed():
            time.sleep_ms(10)
        return True
    return False

def wait_or_stop(ms):
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < ms:
        if c_stop_check():
            return False
        time.sleep_ms(1)
    return True

def move_to(m, target):
    while cur[m] != target:
        if c_stop_check():
            return False
        d = target - cur[m]
        if d > STEP_DEG:
            d = STEP_DEG
        elif d < -STEP_DEG:
            d = -STEP_DEG
        servo_angle(m, cur[m] + d)
        time.sleep_ms(STEP_MS)
    return True

def run_motor(m, tag):
    show(tag, mname(m) + T(' 翻起', ' up'), '', 0xFFFF00)
    print(NAMES_ZH[m], '翻起到', ANGLE_FOLD[m], '度')
    if cur[m] is None:
        servo_angle(m, ANGLE_HOME[m])      # 位置未知：板子應已扶回原位
        if not wait_or_stop(500):
            return False
    if not move_to(m, ANGLE_FOLD[m]) or not wait_or_stop(HOLD_MS):
        return False
    show(tag, mname(m) + T(' 放下', ' down'), '', 0xFFFF00)
    print(NAMES_ZH[m], '放下到', ANGLE_HOME[m], '度')
    return move_to(m, ANGLE_HOME[m])

def spray(tag):
    # 水泵噴 PUMP_SECONDS 秒，每秒更新倒數。被急停回傳 False（stop_all 已關泵）
    if not USE_PUMP:
        return True
    print('開始噴水', PUMP_SECONDS, '秒')
    pump(True)
    spray_motor(True)                      # 水泵和 3 號 360 度馬達同時運轉
    for left in range(PUMP_SECONDS, 0, -1):
        show(T('噴水中', 'SPRAY'), tag, '%d s' % left, 0x00AAFF)
        if not wait_or_stop(1000):
            return False
    pump(False)
    spray_motor(False)
    return True

def fold_only(tag):
    # 只摺衣（馬達一、二、一、三），不沖水。被急停回傳 False
    for m in STEPS:
        if not run_motor(m, tag) or not wait_or_stop(PAUSE_MS):
            return False
    return True

def fold_all(tag):
    # 先噴水，再一次摺完：馬達一、二、一、三。被急停回傳 False
    if not spray(tag):
        return False
    for m in STEPS:
        if not run_motor(m, tag) or not wait_or_stop(PAUSE_MS):
            return False
    return True

def handle_order(oid):
    tag = T('訂單', 'ORDER') + ' #%d' % oid
    report_fold(oid, 'folding')            # 網頁顯示「機台正在摺衣中」
    if fold_all(tag):
        stop_all()                         # 摺完放鬆，不讓馬達一直頂著
        ok = report_fold(oid, 'done')      # 網站訂單立刻完成
        show(T('做完了', 'DONE!'), tag, T('已通知網站', 'site notified') if ok else T('通知網站失敗', 'notify failed'),
             0x00FF00 if ok else 0xFF8800)
        print('訂單', oid, '摺衣做完了')
    else:
        show(T('緊急停止', 'E-STOP'), T('請把摺板扶回原位', 'Put boards back'), '', 0xFF0000)
        print('緊急停止：請把摺板扶回原位')
        time.sleep_ms(2500)

# ================= 主程式 =================
try:
    # 剛開機或繼電器剛動作時電源可能不穩，掃不到就多等幾次再放棄
    _n = 0
    while True:
        _n += 1
        try:
            found = i2c.scan()
        except Exception:
            found = []
        if PCA_ADDR in found:
            break
        if _n % 5 == 0:
            i2c = make_i2c()               # 每 5 次重建一次 I2C 匯流排
        if _n <= 20:
            show(T('找Servo2', 'SCAN'), '%d/20' % _n, '', 0xFFFF00)
            time.sleep_ms(500)
        else:                              # 20 次後不放棄，每 3 秒重掃，Servo2 一出現就繼續
            show(T('找不到Servo2', 'NO SERVO2'), T('檢查電源/接線', 'Check power/wiring'),
                 'I2C: %s' % ([hex(a) for a in found],), 0xFF0000)
            print('找不到 Servo2（0x40），I2C 掃到：', [hex(a) for a in found])
            time.sleep_ms(3000)
    pca_begin()
except Exception as e:
    show('ERROR', str(e)[:30], '', 0xFF0000)
    print('啟動錯誤：', e)
    while True:
        time.sleep_ms(1000)

if not connect():
    wifi_diag()
    while True:
        time.sleep_ms(1000)

since = None
next_poll = 0
show(T('等待送洗', 'READY'), T('網站已連線', 'ONLINE'), '', 0x00FF66)
try:
    speaker.tone(2000, 100)
except Exception:
    pass

while True:
    if btnA.wasPressed():
        next_poll = 0
    if btnB.wasPressed():                      # B：單獨摺衣（不沖水、不回報網站）
        fold_only(T('單獨摺衣', 'FOLD'))
        stop_all()
        show(T('等待送洗', 'READY'), T('網站已連線', 'ONLINE'), '', 0x00FF66)
    if btnC.wasPressed():                      # C：待機時按也確保全部關閉
        stop_all()

    if time.ticks_diff(time.ticks_ms(), next_poll) >= 0:
        next_poll = time.ticks_add(time.ticks_ms(), POLL_MS)
        try:
            data = api_get('/api/device/poll' + ('' if since is None else '?since=%d' % since))
            since = data['last_id']
            for ev in data['events']:
                if ev['kind'] == 'drop' and ev.get('order_id'):
                    handle_order(ev['order_id'])
                    next_poll = time.ticks_add(time.ticks_ms(), POLL_MS)
            if data['events']:
                show(T('等待送洗', 'READY'), T('網站已連線', 'ONLINE'), '', 0x00FF66)
        except Exception as e:
            print('poll failed:', e)
            show(T('網路錯誤', 'NET ERR'), str(e)[:24], '', 0xFF8800)
            if not wifiCfg.wlan_sta.isconnected():
                connect()
    time.sleep_ms(20)
