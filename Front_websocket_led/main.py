"""
===========================================================================================
 PROYECTO: Control de RELÉ (ESP32) mediante WebSocket en tiempo real (Frontend Flet)
===========================================================================================

DESCRIPCIÓN
-----------
Este frontend (Flet) permite controlar un relé conectado a uno o varios ESP32
a través de un servidor WebSocket (FastAPI). La comunicación es bidireccional y
en tiempo real: el usuario envía comandos y recibe confirmaciones de estado.

ARQUITECTURA
------------
        ┌───────────────┐        ┌──────────────────────┐        ┌───────────────┐
        │   FRONTEND     │ <----> │ SERVIDOR WEBSOCKET   │ <----> │    ESP32(s)    │
        │  (Flet UI)     │        │   (FastAPI /ws)      │        │ (WS/WSS client)│
        └───────────────┘        └──────────────────────┘        └───────────────┘

PROTOCOLO JSON
--------------
(ver README del proyecto)

===========================================================================================
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
import os
from typing import Callable, Optional

import flet as ft
import websockets
from dotenv import load_dotenv


# ===============================================================================
# 🌐 Cargar configuración desde archivo .env
# ===============================================================================
load_dotenv()

PORT = int(os.environ.get("PORT", 0))
WEBSOCKET_URL = os.getenv("WEBSOCKET_URL")

if not WEBSOCKET_URL:
    raise ValueError("⚠️ ERROR: La variable WEBSOCKET_URL no está definida en .env")


# ===============================================================================
# 🧠 CLASE CLIENTE WEBSOCKET
# ===============================================================================
class WebSocketClient:
    """
    Cliente WebSocket reutilizable para el frontend.
    """

    def __init__(self, ui_callback: Callable[[dict], None]):
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.ui_callback = ui_callback
        self._stop = False

    async def connect_forever(self):
        while not self._stop:
            try:
                await self._connect_once()
                await asyncio.sleep(1)
            except Exception as e:
                print(f"{datetime.now()} ❌ Error WS: {e}")
                await asyncio.sleep(2)

    async def _connect_once(self):
        # print(f"{datetime.now()} 🔌 Conectando a {WEBSOCKET_URL}")
        self.websocket = await websockets.connect(WEBSOCKET_URL)
        # print(f"{datetime.now()} ✅ Conectado")

        await self.send_json({"type": "register", "role": "frontend"})
        await self.listen_loop()

    async def listen_loop(self):
        assert self.websocket
        try:
            while True:
                raw = await self.websocket.recv()
                # print(f"{datetime.now()} 📩 {raw}")
                data = json.loads(raw)
                if isinstance(data, dict):
                    self.ui_callback(data)
        except Exception as e:
            print(f"{datetime.now()} ❌ WS cerrado: {e}")

    async def send_json(self, payload: dict):
        if not self.websocket:
            return
        await self.websocket.send(json.dumps(payload))

        # ✅ SOLO imprimir comandos de encender / apagar (type="command")
        if payload.get("type") == "command":
            print(
                f"{datetime.now()} 🎮 CMD enviado -> "
                f"to={payload.get('to')} {payload.get('device')}[{payload.get('id')}] {payload.get('action')}"
            )

    async def request_state(self, esp32_id: str):
        await self.send_json({"type": "get_state", "to": esp32_id})

    async def command_relay(self, esp32_id: str, action: str):
        await self.send_json({
            "type": "command",
            "to": esp32_id,
            "device": "relay",
            "id": 0,
            "action": action,
        })


# ===============================================================================
# 🖥️ INTERFAZ PRINCIPAL
# ===============================================================================
def main(page: ft.Page):

    page.title = "Control Relé ESP32"
    page.vertical_alignment = ft.MainAxisAlignment.CENTER

    selected_esp32: Optional[str] = None

    title = ft.Text("Control de Luz (ESP32)", size=22, weight=ft.FontWeight.BOLD)
    ws_info = ft.Text(f"Servidor: {WEBSOCKET_URL}", size=12)

    status_text = ft.Text("Esperando conexión...", size=16)

    esp32_dropdown = ft.Dropdown(label="ESP32 destino", width=260)

    relay_switch = ft.Switch(label="ON / OFF", value=False)

    bulb_icon = ft.Icon(
        name=ft.Icons.LIGHTBULB_OUTLINE,
        size=48,
        color=ft.Colors.GREY_500,
    )

    # --------------------------------------------------------------------------
    # Callback de mensajes WS
    # --------------------------------------------------------------------------
    def update_status(data: dict):
        nonlocal selected_esp32

        msg_type = data.get("type")

        if msg_type == "ping":
            return

        if msg_type == "registered":
            status_text.value = "✅ Frontend conectado"
            page.update()
            return

        if msg_type == "esp32_list":
            items = data.get("items", [])
            esp32_dropdown.options = [ft.dropdown.Option(x) for x in items]

            if not selected_esp32 and items:
                selected_esp32 = items[0]
                esp32_dropdown.value = selected_esp32
                page.run_task(ws_client.request_state, selected_esp32)

            page.update()
            return

        if msg_type == "state":
            st = data.get("state")

            if st == "on":
                relay_switch.value = True
                bulb_icon.name = ft.Icons.LIGHTBULB
                bulb_icon.color = ft.Colors.AMBER_500
                status_text.value = "💡 Luz encendida"
            else:
                relay_switch.value = False
                bulb_icon.name = ft.Icons.LIGHTBULB_OUTLINE
                bulb_icon.color = ft.Colors.GREY_500
                status_text.value = "⚫ Luz apagada"

            page.update()
            return

    ws_client = WebSocketClient(update_status)

    # --------------------------------------------------------------------------
    # Eventos UI
    # --------------------------------------------------------------------------
    async def on_select_esp32(e):
        nonlocal selected_esp32
        selected_esp32 = e.control.value
        await ws_client.request_state(selected_esp32)

    esp32_dropdown.on_change = lambda e: page.run_task(on_select_esp32, e)

    async def on_switch_changed(e):
        if not selected_esp32:
            relay_switch.value = False
            page.update()
            return

        action = "on" if e.control.value else "off"
        await ws_client.command_relay(selected_esp32, action)

    relay_switch.on_change = lambda e: page.run_task(on_switch_changed, e)

    # --------------------------------------------------------------------------
    # Layout
    # --------------------------------------------------------------------------
    page.add(
        ft.Column(
            [
                title,
                ws_info,
                ft.Divider(),
                esp32_dropdown,
                bulb_icon,
                relay_switch,
                ft.Divider(),
                status_text,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
    )

    page.run_task(ws_client.connect_forever)


# ===============================================================================
# 🚀 EJECUCIÓN
# ===============================================================================
if __name__ == "__main__":
    if PORT == 0:
        ft.app(target=main, view=ft.AppView.WEB_BROWSER)
    else:
        ft.app(target=main, port=PORT)
