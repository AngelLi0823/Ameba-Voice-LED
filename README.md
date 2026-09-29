# Ameba Mini 語音 LED 控制

## 程式檔案

- `ameba_voice_led.ino`：AMB82 Mini 開發板端程式
- `voice_control.py`：Windows 電腦端語音辨識與控制程式

## 執行環境

- Windows
- AmebaPro2 AMB82 Mini（RTL8735B）
- Arduino IDE
- Python
- USB 序列埠：COM5
- 傳輸速率：115200 bps

## Python 套件

- pyserial
- SpeechRecognition
- PyAudio

## 執行方式

1. 使用 Arduino IDE 將 `ameba_voice_led.ino` 燒錄至 AMB82 Mini。
2. 安裝 Python 所需套件。
3. 關閉 Arduino IDE 的序列監控視窗。
4. 執行 `voice_control.py`。
5. 按下「開始語音辨識」並說出控制指令。# Ameba-Voice-LED
Voice-controlled LED system for AmebaPro2 AMB82 Mini
