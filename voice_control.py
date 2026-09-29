"""使用電腦麥克風辨識中英文，並透過序列埠控制 Ameba Mini LED。"""

import base64
import queue
import subprocess
import threading #多執行緒，讓語音辨識、序列埠通訊和畫面可以同時運作，避免介面卡住
import time
import tkinter as tk
from tkinter import ttk

import serial #匯入 pyserial，用來透過 COM5 和 Ameba Mini 通訊
import speech_recognition as sr


# ===== 可依電腦環境調整的設定 =====
SERIAL_PORT = "COM5"
BAUD_RATE = 115200
SERIAL_TIMEOUT = 0.5
LANGUAGE = "zh-TW"
RECOGNITION_LANGUAGES = (LANGUAGE, "en-US")

# 電腦與開發板之間使用簡單、易除錯的逐行文字指令。
VOICE_COMMANDS = {
    "左邊開燈": "LEFT_ON",
    "右邊開燈": "RIGHT_ON",
    "通訊中斷": "DISCONNECT",
    "閃爍三次": "BLINK_THREE",
    "閃爍3次": "BLINK_THREE",
    "turn on the left light": "LEFT_ON",
    "turn on the right light": "RIGHT_ON",
    "disconnect communication": "DISCONNECT",
}
VOICE_REPLIES = {
    "LEFT_ON": ("BLUE", "藍燈已開啟"),
    "RIGHT_ON": ("GREEN", "綠燈已開啟"),
    "BLINK_THREE": (None, "已閃爍三次"),
}


class VoiceLedApp:
    """管理圖形介面、語音辨識及序列埠通訊。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Ameba Mini 中英文語音 LED 控制")
        self.root.geometry("720x560")
        self.root.minsize(720, 560)

        self.serial_port = None
        self.running = True
        self.listening = False
        self.ui_queue = queue.Queue()
        self.serial_lock = threading.Lock()
        self.communication_enabled = True
        self.connection_generation = 0
        self.pending_replies = {}
        self.speech_queue = queue.Queue()

        self.recognizer = sr.Recognizer()
        # 適度延長停頓時間，可減少中文語句尚未說完就停止錄音的情況。
        self.recognizer.pause_threshold = 0.8

        self.recognition_var = tk.StringVar(value="尚未辨識")
        self.send_var = tk.StringVar(value="尚未傳送指令")
        self.connection_var = tk.StringVar(value="尚未連線")
        self.blue_var = tk.StringVar(value="未知")
        self.green_var = tk.StringVar(value="未知")
        self.speech_var = tk.StringVar(value="等待開燈回覆")

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._process_ui_queue)
        threading.Thread(target=self._speech_worker, daemon=True).start()

        # 序列埠連線可能稍有延遲，因此放到背景執行，避免介面卡住。
        threading.Thread(target=self.connect_serial, daemon=True).start()

    def _build_ui(self) -> None:
        """建立所有圖形介面元件。"""
        main = ttk.Frame(self.root, padding=18)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Ameba Mini 語音 LED 控制", font=("Microsoft JhengHei UI", 18, "bold")).grid(
            row=0, column=0, columnspan=2, pady=(0, 18)
        )

        labels = [
            ("COM5 連線狀態：", self.connection_var),
            ("語音辨識結果：", self.recognition_var),
            ("指令傳送狀態：", self.send_var),
            ("藍燈實際狀態：", self.blue_var),
            ("綠燈實際狀態：", self.green_var),
            ("語音回覆狀態：", self.speech_var),
        ]
        for row, (caption, variable) in enumerate(labels, start=1):
            ttk.Label(main, text=caption, font=("Microsoft JhengHei UI", 11, "bold")).grid(
                row=row, column=0, sticky="ne", padx=(0, 10), pady=7
            )
            ttk.Label(main, textvariable=variable, wraplength=440, font=("Microsoft JhengHei UI", 11)).grid(
                row=row, column=1, sticky="nw", pady=7
            )

        button_frame = ttk.Frame(main)
        button_frame.grid(row=7, column=0, columnspan=2, pady=(20, 10))
        self.listen_button = ttk.Button(button_frame, text="開始語音辨識", command=self.start_listening)
        self.listen_button.pack(side="left", padx=6)
        ttk.Button(button_frame, text="重新連線 COM5", command=self.reconnect_serial).pack(side="left", padx=6)
        ttk.Button(button_frame, text="查詢 LED 狀態", command=self.query_state).pack(side="left", padx=6)

        ttk.Label(
            main,
            text=("每按一次按鈕辨識一句，中英文皆可：\n"
                  "左邊開燈 / turn on the left light → 藍燈\n"
                  "右邊開燈 / turn on the right light → 綠燈\n"
                  "通訊中斷 / disconnect communication\n"
                  "閃爍三次 → 藍綠燈同步閃爍，第三次亮三秒後自動關閉\n"
                  "通訊中斷後無法開燈；請按「重新連線 COM5」恢復控制。"),
            justify="center",
            foreground="#555555",
            font=("Microsoft JhengHei UI", 10),
        ).grid(row=8, column=0, columnspan=2, pady=(10, 0))

    def _speech_worker(self) -> None:
        """使用 Windows 內建語音合成，在背景依序播放中文回覆。"""
        while self.running:
            reply = self.speech_queue.get()
            if reply is None or not self.running:
                return
            # 回覆僅來自 VOICE_REPLIES；以 UTF-16 編碼保留中文字元。
            script = (
                "$ErrorActionPreference = 'Stop'; "
                "Add-Type -AssemblyName System.Speech; "
                "$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "try { "
                "$speaker.SelectVoiceByHints("
                "[System.Speech.Synthesis.VoiceGender]::NotSet, "
                "[System.Speech.Synthesis.VoiceAge]::NotSet, 0, "
                "[System.Globalization.CultureInfo]::GetCultureInfo('zh-TW')); "
                "$speaker.SetOutputToDefaultAudioDevice(); "
                f"$speaker.Speak('{reply}'); "
                "} finally { $speaker.Dispose() }"
            )
            encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
            self._post(self._set_var, self.speech_var, f"正在播放：{reply}")
            try:
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                    check=True, capture_output=True, timeout=20,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self._post(self._set_var, self.speech_var, f"已播放：{reply}")
            except (OSError, subprocess.SubprocessError) as exc:
                self._post(
                    self._set_var, self.speech_var,
                    f"語音播放失敗，請確認 Windows 中文語音及喇叭設定（{type(exc).__name__}）",
                )

    def _post(self, action, *args) -> None:
        """將背景執行緒的介面更新安全地送回主執行緒。"""
        self.ui_queue.put((action, args))

    def _process_ui_queue(self) -> None:
        """定期處理背景執行緒送來的介面更新。"""
        try:
            while True:
                action, args = self.ui_queue.get_nowait()
                action(*args)
        except queue.Empty:
            pass
        if self.running:
            self._check_reply_timeouts()
            self.root.after(100, self._process_ui_queue)

    def _check_reply_timeouts(self) -> None:
        """沒有收到完成回報時明確提示，不把送出指令當作執行成功。"""
        with self.serial_lock:
            expired = [command for command, deadline in self.pending_replies.items()
                       if time.monotonic() > deadline]
            for command in expired:
                del self.pending_replies[command]
            if expired:
                commands = "、".join(expired)
                self._post(self._set_var, self.send_var,
                           f"回報逾時（{commands}）：未確認執行完成，請按「查詢 LED 狀態」檢查通訊")
                self._post(self._set_var, self.speech_var, "未收到完成確認，不播放成功回覆")

    def _set_var(self, variable: tk.StringVar, value: str) -> None:
        variable.set(value)

    def _set_listen_button(self, enabled: bool) -> None:
        self.listening = not enabled
        self.listen_button.configure(state="normal" if enabled else "disabled")

    def connect_serial(self) -> None:
        """連接 COM5，成功後啟動接收執行緒並查詢 LED 狀態。"""
        with self.serial_lock:
            if not self.running or not self.communication_enabled:
                return
            generation = self.connection_generation
        self._post(self._set_var, self.connection_var, f"正在連線 {SERIAL_PORT}…")
        try:
            port = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=SERIAL_TIMEOUT)
            # 某些 Arduino 相容板開啟序列埠時會重置；清掉重置期間的舊資料。
            port.reset_input_buffer()
            with self.serial_lock:
                if (not self.running or not self.communication_enabled
                        or generation != self.connection_generation):
                    port.close()
                    return
                self.serial_port = port
            self._post(self._set_var, self.connection_var, f"已開啟 {SERIAL_PORT}，等待開發板回報（{BAUD_RATE} bps）")
            threading.Thread(target=self._serial_reader, args=(port,), daemon=True).start()
            # 板子啟動後立即取得兩顆 LED 的目前狀態。
            self._send_line("QUERY_STATE", show_status=False)
        except (serial.SerialException, OSError) as exc:
            self._post(self._set_var, self.connection_var, f"連線失敗：{exc}")
            self._post(self._set_var, self.send_var, "無法傳送：請確認 COM5 未被其他程式占用且開發板已連接")

    def reconnect_serial(self) -> None:
        """關閉舊連線後重新連接。"""
        self._close_serial()
        with self.serial_lock:
            self.communication_enabled = True
        self.connection_var.set("準備重新連線…")
        threading.Thread(target=self.connect_serial, daemon=True).start()

    def disconnect_serial(self) -> None:
        """語音中斷通訊後，必須手動重新連線才能恢復控制。"""
        self._close_serial()
        self._post(self._set_var, self.connection_var, "通訊已中斷")
        self._post(self._set_var, self.send_var, "開燈控制已停用，請按「重新連線 COM5」恢復")
        self._post(self._set_var, self.blue_var, "未知（通訊已中斷）")
        self._post(self._set_var, self.green_var, "未知（通訊已中斷）")

    def _close_serial(self) -> None:
        """安全關閉目前序列埠。"""
        with self.serial_lock:
            self.communication_enabled = False
            self.connection_generation += 1
            port = self.serial_port
            self.serial_port = None
            self.pending_replies.clear()
        if port is not None:
            try:
                port.close()
            except (serial.SerialException, OSError):
                pass

    def _serial_reader(self, port: serial.Serial) -> None:
        """持續接收板子回傳的逐行訊息。"""
        while self.running and port.is_open:
            try:
                raw = port.readline()
                if not raw:
                    continue
                message = raw.decode("utf-8", errors="replace").strip()
                with self.serial_lock:
                    if self.serial_port is not port:
                        break
                if message:
                    self._handle_board_message(message)
            except (serial.SerialException, OSError) as exc:
                with self.serial_lock:
                    if self.serial_port is not port:
                        break
                    self.serial_port = None
                    self.pending_replies.clear()
                if self.running:
                    self._post(self._set_var, self.connection_var, f"通訊中斷：{exc}")
                    self._post(self._set_var, self.send_var, "傳送失敗：序列埠連線已中斷")
                break

    def _handle_board_message(self, message: str) -> None:
        """解析板子回傳的實際 LED 狀態或錯誤。"""
        if message.startswith("STATE "):
            fields = {}
            for item in message[6:].split():
                if "=" in item:
                    key, value = item.split("=", 1)
                    fields[key] = value
            if fields.get("BLUE") in ("ON", "OFF") and fields.get("GREEN") in ("ON", "OFF"):
                self._post(self._set_var, self.connection_var, f"已連線 {SERIAL_PORT}，已收到燈號回報")
                blue = "開啟" if fields["BLUE"] == "ON" else "關閉"
                green = "開啟" if fields["GREEN"] == "ON" else "關閉"
                self._post(self._set_var, self.blue_var, blue)
                self._post(self._set_var, self.green_var, green)
                self._post(self._set_var, self.send_var, f"開發板已回報狀態：藍燈{blue}、綠燈{green}")
                with self.serial_lock:
                    for command, deadline in list(self.pending_replies.items()):
                        color, reply = VOICE_REPLIES[command]
                        if time.monotonic() <= deadline and fields.get(color) == "ON":
                            del self.pending_replies[command]
                            self.speech_queue.put(reply)
        elif message == "DONE BLINK_THREE":
            with self.serial_lock:
                deadline = self.pending_replies.pop("BLINK_THREE", None)
                if deadline is not None and time.monotonic() <= deadline:
                    self.speech_queue.put(VOICE_REPLIES["BLINK_THREE"][1])
                    self._post(self._set_var, self.send_var, "已閃爍三次，藍燈與綠燈已自動關閉")
        elif message.startswith("ERROR "):
            with self.serial_lock:
                self.pending_replies.clear()
            self._post(self._set_var, self.send_var, f"開發板錯誤：{message[6:]}")

    def _send_line(self, command: str, show_status: bool = True) -> bool:
        """傳送一行 ASCII 指令；失敗時顯示明確錯誤。"""
        with self.serial_lock:
            if not self.communication_enabled:
                self._post(self._set_var, self.send_var, "通訊已中斷，未傳送指令；請先重新連線 COM5")
                return False
            port = self.serial_port
            if port is None or not port.is_open:
                self._post(self._set_var, self.send_var, f"傳送失敗：{SERIAL_PORT} 尚未連線")
                return False
            try:
                blink_deadline = self.pending_replies.get("BLINK_THREE", 0)
                if command in VOICE_REPLIES and time.monotonic() <= blink_deadline:
                    self._post(self._set_var, self.send_var, "正在閃爍三次，請等待完成後再下指令")
                    return False
                if command in VOICE_REPLIES:
                    self.pending_replies[command] = time.monotonic() + (10 if command == "BLINK_THREE" else 5)
                port.write((command + "\n").encode("ascii"))
                port.flush()
            except (serial.SerialException, OSError) as exc:
                self.pending_replies.pop(command, None)
                self._post(self._set_var, self.send_var, f"傳送失敗：{exc}")
                self._post(self._set_var, self.connection_var, f"通訊錯誤：{exc}")
                return False
        if show_status:
            self._post(self._set_var, self.send_var, f"已傳送 {command}，等待開發板回報實際狀態…")
        return True

    def query_state(self) -> None:
        """手動要求板子回報兩顆 LED 狀態。"""
        self._send_line("QUERY_STATE")

    def start_listening(self) -> None:
        """啟動一次背景語音辨識。"""
        if self.listening:
            return
        self._set_listen_button(False)
        self.recognition_var.set("正在聆聽，請說出指令…")
        threading.Thread(target=self._recognize_once, daemon=True).start()

    @staticmethod
    def _normalize_text(text: str) -> str:
        """忽略英文大小寫，以及辨識服務加入的空白與標點。"""
        for char in (" ", "，", "。", ",", ".", "！", "!", "？", "?"):
            text = text.replace(char, "")
        return "".join(text.casefold().split())

    def _recognize_command(self, audio):
        """同一段錄音先試中文，未匹配指令時再試英文；只執行一次。"""
        transcripts = []
        request_error = None
        for language in RECOGNITION_LANGUAGES:
            try:
                text = self.recognizer.recognize_google(audio, language=language)
            except sr.UnknownValueError:
                continue
            except sr.RequestError as exc:
                request_error = exc
                continue
            transcripts.append(text)
            normalized = self._normalize_text(text)
            for phrase, command in VOICE_COMMANDS.items():
                if normalized == self._normalize_text(phrase):
                    return text, command
        if request_error is not None:
            raise request_error
        if transcripts:
            return " / ".join(transcripts), None
        raise sr.UnknownValueError()

    def _recognize_once(self) -> None:
        """從預設麥克風錄音，辨識中英文並在完全符合時執行命令。"""
        try:
            with sr.Microphone() as source:
                # 短暫量測環境噪音，改善一般室內的辨識效果。
                self.recognizer.adjust_for_ambient_noise(source, duration=0.6)
                audio = self.recognizer.listen(source, timeout=5, phrase_time_limit=5)

            text, command = self._recognize_command(audio)
            self._post(self._set_var, self.recognition_var, text)

            if command is None:
                self._post(self._set_var, self.send_var, "不是有效控制指令，LED 狀態不變")
            elif command == "DISCONNECT":
                self.disconnect_serial()
                self.speech_queue.put("通訊已中斷")
            else:
                self._send_line(command)
        except sr.WaitTimeoutError:
            self._post(self._set_var, self.recognition_var, "逾時：沒有偵測到語音")
            self._post(self._set_var, self.send_var, "未傳送指令，LED 狀態不變")
        except sr.UnknownValueError:
            self._post(self._set_var, self.recognition_var, "無法辨識語音內容")
            self._post(self._set_var, self.send_var, "未傳送指令，LED 狀態不變")
        except sr.RequestError as exc:
            self._post(self._set_var, self.recognition_var, f"語音辨識服務錯誤：{exc}")
            self._post(self._set_var, self.send_var, "未傳送指令，LED 狀態不變")
        except (OSError, AttributeError) as exc:
            self._post(self._set_var, self.recognition_var, f"麥克風錯誤：{exc}")
            self._post(self._set_var, self.send_var, "未傳送指令，請確認麥克風及 PyAudio 安裝狀態")
        finally:
            self._post(self._set_listen_button, True)

    def close(self) -> None:
        """結束程式並釋放序列埠。"""
        self.running = False
        self.speech_queue.put(None)
        self._close_serial()
        self.root.destroy()


def main() -> None:
    """程式進入點。"""
    root = tk.Tk()
    VoiceLedApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
