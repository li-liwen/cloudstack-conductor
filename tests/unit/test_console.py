from __future__ import annotations

from conductor.execution.console import native_console_websocket_url


def test_console_page_derives_novnc_websocket() -> None:
    page = "http://console.example.com/resource/noVNC/vnc.html?autoconnect=true&show_dot=true&port=8080&token=a+b/c=="
    websocket = native_console_websocket_url(page)
    assert websocket.startswith("ws://console.example.com:8080/websockify/websockify?")
    assert websocket.endswith("?token=a+b/c==")


def test_console_websocket_uses_explicit_path_and_encryption() -> None:
    page = "https://console.example.com/vnc.html?port=8443&path=proxy/ws&token=t&encrypt=1"
    websocket = native_console_websocket_url(page)
    assert websocket == "wss://console.example.com:8443/proxy/ws?token=t"
