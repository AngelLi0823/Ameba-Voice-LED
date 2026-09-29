/*
 * AmebaPro2 AMB82 Mini 語音 LED 控制（開發板端）
 *
 * 序列指令：
 *   LEFT_ON     開啟 LED_BUILTIN 藍燈，三秒後自動關閉
 *   RIGHT_ON    開啟 LED_G 綠燈，三秒後自動關閉
 *   QUERY_STATE 查詢兩顆 LED 的目前實際狀態
 *   BLINK_THREE 藍綠燈同時亮起三次，第三次亮三秒後關閉；完成回傳 DONE BLINK_THREE
 *
 * 每次執行合法指令，以及 LED 自動熄滅時都會回傳：
 *   STATE BLUE=ON|OFF GREEN=ON|OFF
 */

// 依實機確認：板載藍綠 LED 為高電位點亮、低電位熄滅。
const uint8_t LED_ON_LEVEL = HIGH;
const uint8_t LED_OFF_LEVEL = LOW;

// 不論藍燈或綠燈，亮起三秒後都會自動關閉。
const unsigned long LED_ON_DURATION_MS = 3000UL;
const unsigned long BLINK_INTERVAL_MS = 500UL;
bool blinkActive = false;
bool blinkLightsOn = false;
uint8_t blinkCount = 0;
unsigned long blinkChangedAt = 0;

// 分別保存兩顆燈的計時資料，使它們可以獨立運作。
bool blueTimerActive = false;
bool greenTimerActive = false;
unsigned long blueTurnedOnAt = 0;
unsigned long greenTurnedOnAt = 0;

// 累積從電腦收到的一行序列指令。
String serialLine = "";

bool isLedOn(uint8_t pin) {
  // 讀取輸出腳位目前的電位，取得 LED 實際狀態。
  return digitalRead(pin) == LED_ON_LEVEL;
}

void reportLedState() {
  // 每次都一起回報藍燈和綠燈狀態。
  Serial.print("STATE BLUE=");
  Serial.print(isLedOn(LED_BUILTIN) ? "ON" : "OFF");
  Serial.print(" GREEN=");
  Serial.println(isLedOn(LED_G) ? "ON" : "OFF");
}

void executeCommand(String command) {
  // 移除換行前可能出現的空白或歸位字元。
  command.trim();

  if (blinkActive && (command == "LEFT_ON" || command == "RIGHT_ON" || command == "BLINK_THREE")) {
    Serial.println("ERROR BLINK_BUSY");
    return;
  }

  if (command == "BLINK_THREE") {
    blueTimerActive = false;
    greenTimerActive = false;
    blinkActive = true;
    blinkLightsOn = false;
    blinkCount = 0;
    blinkChangedAt = millis();
    // 先熄滅，讓原本已開啟的燈也有三次清楚的亮起動作。
    digitalWrite(LED_BUILTIN, LED_OFF_LEVEL);
    digitalWrite(LED_G, LED_OFF_LEVEL);
    reportLedState();
  } else if (command == "LEFT_ON") {
    digitalWrite(LED_BUILTIN, LED_ON_LEVEL);
    blueTurnedOnAt = millis();
    blueTimerActive = true;
    reportLedState();
  } else if (command == "RIGHT_ON") {
    digitalWrite(LED_G, LED_ON_LEVEL);
    greenTurnedOnAt = millis();
    greenTimerActive = true;
    reportLedState();
  } else if (command == "QUERY_STATE") {
    reportLedState();
  } else if (command.length() > 0) {
    // 未知指令不會改變任何 LED 狀態。
    Serial.print("ERROR UNKNOWN_COMMAND:");
    Serial.println(command);
  }
}

void updateBlink() {
  const unsigned long interval = (blinkLightsOn && blinkCount == 3)
      ? LED_ON_DURATION_MS : BLINK_INTERVAL_MS;
  if (!blinkActive || (unsigned long)(millis() - blinkChangedAt) < interval) {
    return;
  }
  blinkChangedAt = millis();
  if (blinkLightsOn && blinkCount == 3) {
    blinkActive = false;
    blinkLightsOn = false;
    digitalWrite(LED_BUILTIN, LED_OFF_LEVEL);
    digitalWrite(LED_G, LED_OFF_LEVEL);
    reportLedState();
    Serial.println("DONE BLINK_THREE");
    return;
  }
  blinkLightsOn = !blinkLightsOn;
  if (blinkLightsOn) {
    ++blinkCount;
  }
  digitalWrite(LED_BUILTIN, blinkLightsOn ? LED_ON_LEVEL : LED_OFF_LEVEL);
  digitalWrite(LED_G, blinkLightsOn ? LED_ON_LEVEL : LED_OFF_LEVEL);
  reportLedState();
}

void updateAutoOffTimers() {
  const unsigned long now = millis();
  bool stateChanged = false;

  // 使用相減方式比較時間，即使 millis() 溢位也能正常計時。
  if (blueTimerActive && (unsigned long)(now - blueTurnedOnAt) >= LED_ON_DURATION_MS) {
    digitalWrite(LED_BUILTIN, LED_OFF_LEVEL);
    blueTimerActive = false;
    stateChanged = true;
  }

  if (greenTimerActive && (unsigned long)(now - greenTurnedOnAt) >= LED_ON_DURATION_MS) {
    digitalWrite(LED_G, LED_OFF_LEVEL);
    greenTimerActive = false;
    stateChanged = true;
  }

  // 三秒自動關燈後，立即讓電腦端更新顯示狀態。
  if (stateChanged) {
    reportLedState();
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(LED_G, OUTPUT);

  // 開發板啟動時，兩顆 LED 都先保持關閉。
  digitalWrite(LED_BUILTIN, LED_OFF_LEVEL);
  digitalWrite(LED_G, LED_OFF_LEVEL);

  serialLine.reserve(64);
  reportLedState();
}

void loop() {
  // 非阻塞地處理三秒自動關燈，不使用 delay() 阻塞序列通訊。
  updateAutoOffTimers();
  updateBlink();

  while (Serial.available() > 0) {
    const char incoming = (char)Serial.read();

    if (incoming == '\n') {
      executeCommand(serialLine);
      serialLine = "";
    } else if (incoming != '\r') {
      // 限制指令長度，避免異常資料持續占用記憶體。
      if (serialLine.length() < 63) {
        serialLine += incoming;
      } else {
        serialLine = "";
        Serial.println("ERROR COMMAND_TOO_LONG");
      }
    }
  }
}
