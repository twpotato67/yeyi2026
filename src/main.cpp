// 自動摺衣板 — M5Stack Basic V2.7 + Servo2 模組 + MG996R 360 度 x3
//
// 按鍵：
//   待機中   A：開始摺衣   C：進入測試模式
//   任何時候 B：緊急停止
//   測試模式 A：換馬達   C：讓這顆馬達轉一次
//            長按 A：停止點校正   長按 C：離開測試模式
//   停止點校正 A：-5us   C：+5us   B：離開

#include <M5Unified.h>

// ================= 設定值（微調都改這裡） =================

// 每顆馬達接在 Servo2 的哪個接口
const uint8_t SERVO_CH[3] = {0, 1, 2};

// 停止點（脈衝寬度，微秒）。用測試模式的「停止點校正」找出來再填回這裡
int NEUTRAL_US[3] = {1500, 1500, 1500};

// 轉動速度：與停止點相差多少微秒。越大越快，約 400~500 就是全速
const int SPEED_US[3] = {500, 500, 500};

// 翻起方向：+1 或 -1。如果摺板往反方向轉，就把那顆改成相反的數字
const int FOLD_DIR[3] = {+1, +1, +1};

// 翻起 180 度要轉多久（毫秒）
const uint32_t FOLD_MS[3] = {297, 297, 297};

// 放下回原位要轉多久（毫秒）。放下時有重力幫忙，通常會比翻起短一點
const uint32_t RETURN_MS[3] = {297, 297, 297};

// 翻起後在頂端停多久、每個步驟之間停多久（毫秒）
const uint32_t HOLD_MS  = 300;
const uint32_t PAUSE_MS = 500;

// 摺衣順序（0 = 馬達一，1 = 馬達二，2 = 馬達三）
const uint8_t STEPS[] = {0, 1, 0, 2};

// ================= Servo2（PCA9685）控制 =================

const uint8_t  PCA9685_ADDR = 0x40;
const uint32_t I2C_FREQ     = 400000;
const uint8_t  REG_MODE1    = 0x00;
const uint8_t  REG_LED0     = 0x06;
const uint8_t  REG_PRESCALE = 0xFE;

bool pcaWrite(uint8_t reg, const uint8_t* data, size_t len) {
  return M5.In_I2C.writeRegister(PCA9685_ADDR, reg, data, len, I2C_FREQ);
}

bool pcaWrite8(uint8_t reg, uint8_t value) {
  return pcaWrite(reg, &value, 1);
}

// 設定一個接口的 ON/OFF 計數（0~4095），4 個暫存器一次寫入
void pcaSetChannel(uint8_t ch, uint16_t on, uint16_t off) {
  uint8_t data[4] = {
    (uint8_t)(on & 0xFF), (uint8_t)(on >> 8),
    (uint8_t)(off & 0xFF), (uint8_t)(off >> 8),
  };
  pcaWrite(REG_LED0 + 4 * ch, data, 4);
}

// 送出指定寬度的脈衝（50Hz，一個週期 20000 微秒 = 4096 格）
void servoPulse(uint8_t motor, int us) {
  pcaSetChannel(SERVO_CH[motor], 0, (uint32_t)us * 4096 / 20000);
}

// 完全不送脈衝：MG996R 360 收不到訊號就會停住，不受停止點誤差影響
void servoOff(uint8_t motor) {
  pcaSetChannel(SERVO_CH[motor], 0, 0x1000);
}

void stopAll() {
  for (uint8_t i = 0; i < 3; i++) servoOff(i);
}

bool pcaBegin() {
  // 25MHz / (4096 * 50Hz) - 1 = 121
  if (!pcaWrite8(REG_MODE1, 0x10)) return false;  // 先睡眠才能改頻率
  pcaWrite8(REG_PRESCALE, 121);
  pcaWrite8(REG_MODE1, 0x20);                     // 喚醒、自動遞增、關閉 ALLCALL
  delay(1);
  stopAll();
  return true;
}

// ================= 螢幕 =================

void showScreen(const char* title, const char* line1 = "", const char* line2 = "",
                uint16_t color = TFT_WHITE) {
  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setTextColor(color, TFT_BLACK);
  M5.Display.setFont(&fonts::efontTW_24);
  M5.Display.setTextSize(1.5);
  M5.Display.drawString(title, 10, 20);
  M5.Display.setTextSize(1);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.drawString(line1, 10, 100);
  M5.Display.drawString(line2, 10, 140);
}

// ================= 動作 =================

enum Mode { IDLE, TEST, STOPPED };
Mode mode = IDLE;
uint8_t testMotor = 0;

const char* MOTOR_NAME[3] = {"馬達一", "馬達二", "馬達三"};

void showIdle() {
  showScreen("待機中", "A：開始摺衣", "C：測試模式");
}

void showTest() {
  char line[48];
  snprintf(line, sizeof(line), "%s  A:換  C:轉一次", MOTOR_NAME[testMotor]);
  showScreen("測試模式", line, "長按A:校正 長按C:離開", TFT_CYAN);
}

void emergencyStop() {
  stopAll();
  mode = STOPPED;
  showScreen("緊急停止", "請把摺板扶回原位", "再按 A 開始", TFT_RED);
  Serial.println("緊急停止");
}

// 等待指定時間，期間一直檢查 B 鍵。被急停就回傳 false
bool waitOrStop(uint32_t ms) {
  uint32_t start = millis();
  while (millis() - start < ms) {
    M5.update();
    if (M5.BtnB.wasPressed()) {
      emergencyStop();
      return false;
    }
    delay(1);
  }
  return true;
}

// 一顆馬達：翻起 → 停一下 → 放下
bool runMotor(uint8_t m) {
  char line[48];
  Serial.printf("%s 翻起 %lums\n", MOTOR_NAME[m], FOLD_MS[m]);
  snprintf(line, sizeof(line), "%s 動作中", MOTOR_NAME[m]);
  showScreen(line, "翻起…", "B：緊急停止", TFT_YELLOW);
  servoPulse(m, NEUTRAL_US[m] + FOLD_DIR[m] * SPEED_US[m]);
  if (!waitOrStop(FOLD_MS[m])) return false;
  servoOff(m);
  if (!waitOrStop(HOLD_MS)) return false;

  showScreen(line, "放下…", "B：緊急停止", TFT_YELLOW);
  servoPulse(m, NEUTRAL_US[m] - FOLD_DIR[m] * SPEED_US[m]);
  if (!waitOrStop(RETURN_MS[m])) return false;
  servoOff(m);
  return true;
}

void runFoldSequence() {
  for (size_t i = 0; i < sizeof(STEPS); i++) {
    if (!runMotor(STEPS[i])) return;
    if (!waitOrStop(PAUSE_MS)) return;
  }
  mode = IDLE;
  showScreen("完成！", "A：再摺一件", "C：測試模式", TFT_GREEN);
}

// 停止點校正：送出停止點脈衝，用 A/C 調整到馬達完全不動
void calibrateNeutral(uint8_t m) {
  int us = NEUTRAL_US[m];
  char title[48], line[48];
  snprintf(title, sizeof(title), "%s 校正", MOTOR_NAME[m]);
  while (true) {
    servoPulse(m, us);
    snprintf(line, sizeof(line), "停止點：%d us", us);
    showScreen(title, line, "A:-5 C:+5 B:離開", TFT_ORANGE);
    Serial.printf("%s 停止點 = %d\n", MOTOR_NAME[m], us);
    while (true) {
      M5.update();
      if (M5.BtnA.wasPressed()) { us -= 5; break; }
      if (M5.BtnC.wasPressed()) { us += 5; break; }
      if (M5.BtnB.wasPressed()) {
        servoOff(m);
        NEUTRAL_US[m] = us;  // 重開機後會恢復，記得把數字填回設定值
        showTest();
        return;
      }
      delay(5);
    }
  }
}

void setup() {
  M5.begin();
  Serial.begin(115200);
  if (!pcaBegin()) {
    showScreen("找不到 Servo2", "請檢查模組有沒有", "疊好，再重開機", TFT_RED);
    Serial.println("PCA9685 0x40 沒有回應");
    while (true) delay(1000);
  }
  showIdle();
}

void loop() {
  M5.update();

  if (M5.BtnB.wasPressed()) {
    emergencyStop();
    return;
  }

  switch (mode) {
    case IDLE:
    case STOPPED:
      if (M5.BtnA.wasPressed()) {
        mode = IDLE;
        runFoldSequence();
      } else if (mode == IDLE && M5.BtnC.wasPressed()) {
        mode = TEST;
        showTest();
      }
      break;

    case TEST:
      if (M5.BtnA.wasHold()) {
        calibrateNeutral(testMotor);
      } else if (M5.BtnA.wasClicked()) {
        testMotor = (testMotor + 1) % 3;
        showTest();
      } else if (M5.BtnC.wasHold()) {
        mode = IDLE;
        showIdle();
      } else if (M5.BtnC.wasClicked()) {
        if (runMotor(testMotor)) showTest();
      }
      break;
  }
  delay(5);
}
