"""`dayanera` terminal client: offline tests against a mocked backend."""
from __future__ import annotations

import json

import httpx
import pytest
from app import terminal
from app.core.security import CSRF_HEADER, SESSION_COOKIE


@pytest.fixture(autouse=True)
def isolated_session(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return tmp_path / "DAYANERA" / "cli-session"


def _sse_body(*events: tuple[str, dict]) -> bytes:
    return "".join(f"event: {e}\ndata: {json.dumps(d, ensure_ascii=False)}\n\n" for e, d in events).encode()


def _client(handler) -> terminal.Client:
    c = terminal.Client()
    c.http = httpx.Client(base_url="http://127.0.0.1:8000/api/v1", headers=c.http.headers, cookies=c.http.cookies,
                          transport=httpx.MockTransport(handler))
    return c


def test_sse_parser_handles_multiple_events():
    lines = ["event: status", 'data: {"text": "a"}', "", "event: token", 'data: {"text": "b"}', ""]
    assert list(terminal._sse(lines)) == [("status", {"text": "a"}), ("token", {"text": "b"})]


def test_client_sends_csrf_header_and_stored_session(isolated_session):
    isolated_session.parent.mkdir(parents=True)
    isolated_session.write_text("tok123", encoding="utf-8")
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["csrf"] = req.headers.get(CSRF_HEADER)
        seen["cookie"] = req.headers.get("cookie")
        return httpx.Response(200, json={"username": "admin"})

    assert _client(handler).ensure_login()["username"] == "admin"
    assert seen["csrf"] == "1"
    assert f"{SESSION_COOKIE}=tok123" in seen["cookie"]


def test_login_prompt_stores_token(isolated_session, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _prompt: "admin")
    monkeypatch.setattr(terminal.getpass, "getpass", lambda _prompt: "pw")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/auth/me"):
            return httpx.Response(401, json={"detail": "Oturum açmanız gerekiyor."})
        assert json.loads(req.content) == {"username": "admin", "password": "pw"}
        return httpx.Response(200, json={"username": "admin"}, headers={"set-cookie": f"{SESSION_COOKIE}=newtok; Path=/"})

    _client(handler).ensure_login()
    assert isolated_session.read_text(encoding="utf-8") == "newtok"


def test_login_prompt_without_stdin_exits_cleanly(monkeypatch):
    def no_input(_prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", no_input)
    c = _client(lambda req: httpx.Response(401, json={"detail": "x"}))
    with pytest.raises(SystemExit) as exc:
        c.ensure_login()
    assert exc.value.code == 1


def test_ask_streams_tokens_and_prints_answer_label(capsys):
    body = _sse_body(("user_message", {}), ("status", {"text": "Yerel model yanıtlıyor…"}),
                     ("token", {"text": "Merha"}), ("token", {"text": "ba"}),
                     ("assistant_message", {"content": "Merhaba", "answer_mode_label": "Genel sohbet"}),
                     ("done", {}))
    _client(lambda req: httpx.Response(200, content=body)).ask("c1", "selam")
    out = capsys.readouterr().out
    assert "Merhaba" in out and "[Genel sohbet]" in out
    assert "doğrulanmış son hali" not in out


def test_ask_shows_final_text_when_it_differs_from_stream(capsys):
    body = _sse_body(("token", {"text": "taslak"}), ("assistant_message", {"content": "düzeltilmiş"}))
    _client(lambda req: httpx.Response(200, content=body)).ask("c1", "soru")
    out = capsys.readouterr().out
    assert "doğrulanmış son hali" in out and "düzeltilmiş" in out


def test_ask_prints_backend_error_detail(capsys):
    _client(lambda req: httpx.Response(409, json={"detail": "Arşivlenmiş konuşma"})).ask("c1", "x")
    assert "Arşivlenmiş konuşma" in capsys.readouterr().out


@pytest.mark.parametrize(("argv", "code", "text"), [
    (["foo"], 2, "Bilinmeyen komut"),
    (["-p"], 2, "Kullanım"),
    (["--help"], 0, "dayanera -p"),
])
def test_argument_handling(argv, code, text, capsys):
    assert terminal.main(argv) == code
    captured = capsys.readouterr()
    assert text in captured.out + captured.err


def test_backend_down_message(monkeypatch, capsys):
    def refuse(self, *a, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx.Client, "request", refuse)
    assert terminal.main(["-p", "selam"]) == 1
    assert "dayanera start" in capsys.readouterr().err


def test_logout_removes_stored_session(isolated_session):
    isolated_session.parent.mkdir(parents=True)
    isolated_session.write_text("tok", encoding="utf-8")
    _client(lambda req: httpx.Response(204)).logout()
    assert not isolated_session.exists()
