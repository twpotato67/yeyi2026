"""雨衣清洗＋摺衣站點 app（手機、電腦共用，用瀏覽器開）

執行：python app/server.py
電腦開 http://localhost:8000 ，手機連同一個 WiFi 後開畫面上顯示的網址。
M5Stack 還沒接：硬體由 SimulatedDriver 模擬，之後換成 M5Driver 即可。
加速測試：SIM_SPEED=20 python app/server.py
"""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ================= 設定值 =================
PORT = int(os.environ.get("PORT", "8000"))
SIM_SPEED = float(os.environ.get("SIM_SPEED", "1"))  # 模擬加速倍數
WASH_SECONDS = int(os.environ.get("WASH_SECONDS", "120"))  # 清洗時間（預設兩分鐘）
FOLD_STEPS = [0, 1, 0, 2]           # 摺衣順序：馬達一、二、一、三（同系統規劃）
FOLD_MS, HOLD_MS, RETURN_MS, PAUSE_MS = 297, 300, 297, 500  # 同 uiflow/main.py
STATIONS = [
    {"id": "wash", "name": "洗衣站", "kind": "wash"},
    {"id": "fold", "name": "摺衣站", "kind": "fold"},
]
STATIC_DIR = Path(__file__).parent / "static"


# ================= 硬體介面（之後接 M5Stack 改這裡） =================
class SimulatedDriver:
    """模擬硬體：只回傳每個動作要花多久，不真的控制任何東西。"""

    def wash_seconds(self):
        return WASH_SECONDS

    def fold_step_seconds(self, motor):
        return (FOLD_MS + HOLD_MS + RETURN_MS + PAUSE_MS) / 1000

    def emergency_stop(self):
        pass


class M5Driver(SimulatedDriver):
    """預留：M5Stack 完成後，用 WiFi 呼叫它的 HTTP/MQTT 介面（例如開始、緊急停止）。"""

    def __init__(self, host):
        self.host = host


driver = SimulatedDriver()


# ================= 狀態 =================
lock = threading.RLock()
now = lambda: time.monotonic() * SIM_SPEED  # noqa: E731  模擬時鐘
stations = {
    s["id"]: {**s, "state": "idle", "job": None, "count": 0, "estop": False}
    for s in STATIONS
}
jobs = []        # 所有任務（新的在後面）
next_id = [1]
# 每個站點目前的計時：{station_id: {"start", "duration", "step", "step_start", "step_len"}}
timers = {}

STAGE_TEXT = {
    "washing": "清洗中",
    "wait_move": "請移到摺衣站",
    "wait_fold": "等摺衣站空出來",
    "folding": "摺衣中",
    "done": "完成，可以取件",
    "stopped": "已緊急停止",
    "cancelled": "已取消",
}


def log(job, text):
    job["log"].append({"t": time.strftime("%H:%M:%S"), "text": text})


def find_job(job_id):
    return next((j for j in jobs if j["id"] == job_id), None)


def start_wash(job):
    st = stations["wash"]
    st["state"], st["job"] = "running", job["id"]
    timers["wash"] = {"start": now(), "duration": driver.wash_seconds()}
    job["stage"] = "washing"
    log(job, "開始清洗")


def start_fold(job):
    st = stations["fold"]
    st["state"], st["job"] = "running", job["id"]
    timers["fold"] = {"step": 0, "step_start": now(),
                      "step_len": driver.fold_step_seconds(FOLD_STEPS[0])}
    job["stage"] = "folding"
    log(job, "開始摺衣")


def release(station_id):
    st = stations[station_id]
    st["state"], st["job"] = "idle", None
    timers.pop(station_id, None)


def schedule():
    """空出來的站點，接手排隊中的任務。"""
    if stations["fold"]["state"] == "idle" and not stations["fold"]["estop"]:
        for j in jobs:
            if j["stage"] == "wait_fold":
                start_fold(j)
                break


def tick():
    with lock:
        t = now()
        w = timers.get("wash")
        if w and t - w["start"] >= w["duration"]:
            job = find_job(stations["wash"]["job"])
            job["stage"] = "wait_move"
            log(job, "清洗完成，等待移到摺衣站")
            release("wash")
            stations["wash"]["count"] += 1
        f = timers.get("fold")
        if f and t - f["step_start"] >= f["step_len"]:
            job = find_job(stations["fold"]["job"])
            f["step"] += 1
            if f["step"] >= len(FOLD_STEPS):
                job["stage"] = "done"
                log(job, "摺衣完成")
                release("fold")
                stations["fold"]["count"] += 1
            else:
                f["step_start"] = t
                f["step_len"] = driver.fold_step_seconds(FOLD_STEPS[f["step"]])
        schedule()


def worker():
    while True:
        tick()
        time.sleep(0.2)


# ================= 動作（API 用） =================
def new_job():
    with lock:
        if stations["wash"]["state"] != "idle" or stations["wash"]["estop"]:
            return None, "洗衣站忙碌中或在緊急停止狀態"
        job = {"id": next_id[0], "stage": "washing", "created": time.strftime("%H:%M:%S"),
               "log": []}
        next_id[0] += 1
        jobs.append(job)
        start_wash(job)
        return job, None


def confirm_move(job_id):
    with lock:
        job = find_job(job_id)
        if not job or job["stage"] != "wait_move":
            return "這個任務現在不能移動"
        job["stage"] = "wait_fold"
        log(job, "已移到摺衣站")
        schedule()
        return None


def emergency_stop():
    with lock:
        driver.emergency_stop()
        for sid, st in stations.items():
            st["estop"] = True
            job = find_job(st["job"]) if st["job"] else None
            if job:
                job["stage"] = "stopped"
                job["stopped_at"] = sid  # 記住在哪一站停的，重試時從那站重來
                log(job, "緊急停止")
            release(sid)
            st["estop"] = True


def reset_estop():
    with lock:
        for st in stations.values():
            st["estop"] = False
        schedule()


def retry(job_id):
    """緊急停止後重試：洗衣停的重洗；摺衣停的請先把摺板扶回原位，會從頭摺。"""
    with lock:
        job = find_job(job_id)
        if not job or job["stage"] != "stopped":
            return "這個任務不能重試"
        if any(st["estop"] for st in stations.values()):
            return "請先解除緊急停止"
        if job.get("stopped_at") == "wash":
            if stations["wash"]["state"] != "idle":
                return "洗衣站忙碌中"
            start_wash(job)
        else:
            job["stage"] = "wait_fold"
            log(job, "重試摺衣")
            schedule()
        return None


def cancel(job_id):
    with lock:
        job = find_job(job_id)
        if not job or job["stage"] in ("done", "cancelled"):
            return "這個任務不能取消"
        for sid, st in stations.items():
            if st["job"] == job["id"]:
                release(sid)
        job["stage"] = "cancelled"
        log(job, "已取消")
        schedule()
        return None


def pickup(job_id):
    with lock:
        job = find_job(job_id)
        if not job or job["stage"] != "done":
            return "還不能取件"
        job["picked_up"] = True
        log(job, "已取件")
        return None


def snapshot():
    with lock:
        t = now()
        out_st = []
        for s in stations.values():
            item = {k: s[k] for k in ("id", "name", "kind", "state", "job", "count", "estop")}
            tm = timers.get(s["id"])
            if tm and s["id"] == "wash":
                left = max(0, tm["duration"] - (t - tm["start"]))
                item["progress"] = 1 - left / tm["duration"]
                item["detail"] = "剩 %d 秒" % round(left / SIM_SPEED)
            elif tm:
                item["progress"] = (tm["step"] + min(1, (t - tm["step_start"]) / tm["step_len"])) / len(FOLD_STEPS)
                item["detail"] = "第 %d/%d 步（馬達%s）" % (
                    tm["step"] + 1, len(FOLD_STEPS), "一二三"[FOLD_STEPS[tm["step"]]])
            out_st.append(item)
        out_jobs = [{**j, "text": STAGE_TEXT.get(j["stage"], j["stage"])} for j in jobs[-30:]][::-1]
        return {"stations": out_st, "jobs": out_jobs, "wash_seconds": WASH_SECONDS,
                "urls": lan_urls(), "simulated": isinstance(driver, SimulatedDriver)
                and not isinstance(driver, M5Driver)}


_urls = []


def lan_urls():
    """啟動時查一次就快取；每次請求都查會在沒網路時卡住好幾秒。"""
    if not _urls:
        _urls.append("http://localhost:%d" % PORT)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(1)
                s.connect(("10.255.255.255", 1))  # 不會真的送出封包，只是查本機 IP
                _urls.append("http://%s:%d" % (s.getsockname()[0], PORT))
        except OSError:
            pass
    return list(_urls)


# ================= HTTP =================
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/state":
            return self.send_json(snapshot())
        page = STATIC_DIR / ("index.html" if self.path in ("/", "/index.html") else "")
        if page.is_file():
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parts = self.path.strip("/").split("/")  # api / ...
        err = None
        if parts[:2] == ["api", "jobs"] and len(parts) == 2:
            job, err = new_job()
        elif parts[:2] == ["api", "jobs"] and len(parts) == 4 and parts[2].isdigit():
            action = {"move": confirm_move, "retry": retry, "cancel": cancel,
                      "pickup": pickup}.get(parts[3])
            err = action(int(parts[2])) if action else "未知動作"
        elif parts == ["api", "estop"]:
            emergency_stop()
        elif parts == ["api", "reset"]:
            reset_estop()
        else:
            return self.send_json({"error": "not found"}, 404)
        self.send_json({"ok": err is None, "error": err}, 200 if err is None else 409)


class DualStackServer(ThreadingHTTPServer):
    """同時接 IPv4 和 IPv6：瀏覽器開 localhost 會先試 IPv6，只開 IPv4 會多等 2 秒。"""
    address_family = socket.AF_INET6

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def main():
    threading.Thread(target=worker, daemon=True).start()
    try:
        server = DualStackServer(("::", PORT), Handler)
    except OSError:  # 電腦沒開 IPv6 就只用 IPv4
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("雨衣清洗＋摺衣 app 已啟動（模擬模式，未連接 M5Stack）")
    for u in lan_urls():
        print("  ", u)
    print("手機請連同一個 WiFi，開上面第二個網址。Ctrl+C 結束。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
