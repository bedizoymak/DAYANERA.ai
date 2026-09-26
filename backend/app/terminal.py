"""Terminal client: `dayanera` (installed by scripts/install-cli.ps1).

  dayanera                  interactive chat with the local assistant
  dayanera -p "soru"        ask one question, print the answer, exit
  dayanera start [args]     scripts/start-local.ps1 (e.g. -Open, -Dev)
  dayanera stop [args]      scripts/stop-local.ps1
  dayanera logout           end the stored terminal session
  dayanera <ops-command>    any app.cli command (check-config, migrate, sync-status, ...)

The client talks to the running backend over loopback HTTP, so logins, scopes
and audit records are exactly the same as in the browser UI. The session token
is stored per Windows user under %LOCALAPPDATA%\\DAYANERA\\cli-session.
"""
from __future__ import annotations

import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx

from app.core.config import REPO_ROOT, get_settings
from app.core.security import CSRF_HEADER, SESSION_COOKIE

OPS_COMMANDS = {"check-config", "migrate", "seed", "serve", "reindex", "export-openapi",
                "sync-init", "sync-once", "sync-status"}
HELP_REPL = """Komutlar:
  /yeni      yeni konuşma başlat
  /cikis     çık (Ctrl+C veya Ctrl+Z+Enter da olur)
  /yardim    bu yardım
Kaynakları görmek için yanıttan sonra "kaynak ver" yazın."""

DIM, BOLD, CYAN, RED, RESET = "\033[2m", "\033[1m", "\033[36m", "\033[31m", "\033[0m"
CLEAR = "\r\033[K"  # erases the transient status line
TTY = True


class BackendDown(Exception):
    pass


def _session_file() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
    return base / "DAYANERA" / "cli-session"


def _load_token() -> str | None:
    try:
        return _session_file().read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _save_token(token: str) -> None:
    f = _session_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(token, encoding="utf-8")


def _clear_token() -> None:
    _session_file().unlink(missing_ok=True)


class Client:
    def __init__(self) -> None:
        s = get_settings()
        self.http = httpx.Client(base_url=f"http://{s.app_host}:{s.app_port}/api/v1",
                                 headers={"User-Agent": "dayanera-cli", CSRF_HEADER: "1"},
                                 timeout=httpx.Timeout(30, read=None))
        token = _load_token()
        if token:
            self.http.cookies.set(SESSION_COOKIE, token)

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self.http.request(method, path, **kw)
        except httpx.ConnectError as exc:
            raise BackendDown from exc

    def ensure_login(self) -> dict:
        r = self._req("GET", "/auth/me")
        if r.status_code == 200:
            return r.json()
        print(f"{BOLD}DAYANERA.ai girişi{RESET}")
        for _ in range(3):
            try:
                username = input("Kullanıcı adı: ").strip()
                password = getpass.getpass("Parola: ")
            except (EOFError, KeyboardInterrupt):
                print()
                raise SystemExit(1) from None
            self.http.cookies.clear()
            r = self._req("POST", "/auth/login", json={"username": username, "password": password})
            if r.status_code == 200:
                _save_token(r.cookies.get(SESSION_COOKIE) or "")
                return r.json()
            print(f"{RED}{_detail(r)}{RESET}")
        raise SystemExit(1)

    def logout(self) -> None:
        if _load_token():
            try:
                self._req("POST", "/auth/logout")
            except BackendDown:
                pass
        _clear_token()

    def new_conversation(self) -> str:
        r = self._req("POST", "/conversations", json={})
        r.raise_for_status()
        return r.json()["id"]

    def ask(self, conv_id: str, text: str) -> None:
        """Send one message and render the SSE stream as it arrives."""
        streamed, status_shown = "", False
        try:
            with self.http.stream("POST", f"/conversations/{conv_id}/messages/stream",
                                  json={"content": text, "attachment_ids": []}) as r:
                if r.status_code != 200:
                    r.read()
                    print(f"{RED}{_detail(r)}{RESET}")
                    return
                for event, data in _sse(r.iter_lines()):
                    if event == "status" and TTY:
                        print(f"{CLEAR}{DIM}… {data.get('text', '')}{RESET}", end="", flush=True)
                        status_shown = True
                    elif event == "token":
                        if status_shown:
                            print(CLEAR, end="")
                            status_shown = False
                        streamed += data.get("text", "")
                        print(data.get("text", ""), end="", flush=True)
                    elif event == "assistant_message":
                        if status_shown:
                            print(CLEAR, end="")
                        _render_final(data, streamed)
                    elif event == "error":
                        print(f"\n{RED}{data.get('message', 'Hata')}{RESET}")
        except httpx.ConnectError as exc:
            raise BackendDown from exc
        except KeyboardInterrupt:
            print(f"\n{DIM}(iptal edildi){RESET}")


def _sse(lines):
    event, data = "message", []
    for line in lines:
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


def _render_final(msg: dict, streamed: str) -> None:
    content = (msg.get("content") or "").strip()
    if streamed and content != streamed.strip():
        print(f"\n{DIM}— doğrulanmış son hali —{RESET}")
        print(content)
    elif not streamed:
        print(content)
    else:
        print()
    label = msg.get("answer_mode_label")
    meta = [x for x in (label, msg.get("model"),
                        f"{msg['latency_ms'] / 1000:.1f} sn" if msg.get("latency_ms") else None) if x]
    if meta:
        print(f"{DIM}[{' · '.join(meta)}]{RESET}")


def _detail(r: httpx.Response) -> str:
    try:
        return r.json().get("detail") or f"HTTP {r.status_code}"
    except ValueError:
        return f"HTTP {r.status_code}"


def repl(client: Client) -> int:
    user = client.ensure_login()
    print(f"{BOLD}{CYAN}DAYANERA.ai{RESET} {DIM}· yerel mühendislik asistanı · {user.get('display_name') or user['username']}"
          f" · /yardim{RESET}")
    conv_id = None
    while True:
        try:
            text = input(f"\n{BOLD}›{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        cmd = text.lower()
        if cmd in ("/cikis", "/çıkış", "/exit", "/quit"):
            return 0
        if cmd in ("/yardim", "/yardım", "/help"):
            print(HELP_REPL)
            continue
        if cmd in ("/yeni", "/new"):
            conv_id = None
            print(f"{DIM}Yeni konuşma.{RESET}")
            continue
        if conv_id is None:
            conv_id = client.new_conversation()
        client.ask(conv_id, text)


def _run_script(name: str, args: list[str]) -> int:
    script = REPO_ROOT / "scripts" / name
    return subprocess.call(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), *args])


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    if sys.stdout.isatty():
        os.system("")  # enables ANSI escape handling in the Windows console
    else:
        global DIM, BOLD, CYAN, RED, RESET, CLEAR, TTY
        DIM = BOLD = CYAN = RED = RESET = CLEAR = ""
        TTY = False
    if argv and argv[0] in ("-h", "--help", "help"):
        print("\n\n".join(__doc__.split("\n\n")[1:2]))
        return 0
    if argv and argv[0] in ("start", "stop"):
        return _run_script(f"{argv[0]}-local.ps1", argv[1:])
    if argv and argv[0] in OPS_COMMANDS:
        from app.cli import main as ops_main

        return ops_main(argv)
    try:
        client = Client()
        if argv and argv[0] == "logout":
            client.logout()
            print("Oturum kapatıldı.")
            return 0
        if argv and argv[0] in ("-p", "--print"):
            if len(argv) < 2:
                print('Kullanım: dayanera -p "soru"', file=sys.stderr)
                return 2
            client.ensure_login()
            client.ask(client.new_conversation(), " ".join(argv[1:]))
            return 0
        if argv:
            print(f"Bilinmeyen komut: {argv[0]} (yardım: dayanera --help)", file=sys.stderr)
            return 2
        return repl(client)
    except BackendDown:
        print(f"{RED}Arka uç çalışmıyor.{RESET} Başlatmak için: dayanera start", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
