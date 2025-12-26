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
        """Mantiene la conexión abierta y reintenta tras pausas de seguridad."""
        while not self._stop:
            try:
                await self._connect_once()
            except Exception as e:
                print(f"{datetime.now()} ❌ Error crítico de conexión: {e}")
            finally:
                self.websocket = None
                self.ui_callback({"type": "server_disconnected"})
                # PAUSA DE SEGURIDAD: Evita el consumo excesivo de CPU en bucles de reconexión
                await asyncio.sleep(5)

    async def _connect_once(self):
        print(f"{datetime.now()} 🔌 Conectando a {WEBSOCKET_URL}...")
        async with websockets.connect(WEBSOCKET_URL, ping_interval=None) as ws:
            self.websocket = ws
            print(f"{datetime.now()} ✅ Conexión establecida")
            
            await self.send_json({"type": "register", "role": "frontend"})
            
            async for message in ws:
                data = json.loads(message)
                if isinstance(data, dict):
                    self.ui_callback(data)

    async def send_json(self, payload: dict):
        if self.websocket is None:
            return
        
        try:
            await self.websocket.send(json.dumps(payload))
        except Exception as e:
            print(f"{datetime.now()} ❌ Error al enviar JSON: {e}")
            self.websocket = None

    async def request_state(self, esp32_id: str):
        if esp32_id:
            await self.send_json({"type": "get_state", "to": esp32_id})

    async def command_relay(self, esp32_id: str, action: str):
        if esp32_id:
            now = datetime.now().strftime("%H:%M:%S")
            print(f"{now} 🕹️  Enviando comando: {action.upper()} -> {esp32_id}")
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
    page.theme_mode = ft.ThemeMode.DARK
    page.vertical_alignment = ft.MainAxisAlignment.CENTER
    page.padding = 40

    selected_esp32: Optional[str] = None
    is_esp_online = False

    # --- Componentes UI ---
    title = ft.Text("Control de Luz (ESP32)", size=28, weight=ft.FontWeight.BOLD)
    ws_info = ft.Text(f"Servidor: {WEBSOCKET_URL}", size=12, color=ft.Colors.GREY_400)
    
    status_point = ft.Container(width=12, height=12, border_radius=6, bgcolor=ft.Colors.RED_500)
    status_text = ft.Text("Desconectado del servidor", size=14, italic=True)
    server_status_row = ft.Row([status_point, status_text], alignment=ft.MainAxisAlignment.CENTER)

    client_ip_text = ft.Text("Tu IP: detectando...", size=12, color=ft.Colors.BLUE_200)

    esp32_dropdown = ft.Dropdown(
        label="Seleccionar ESP32", 
        width=260, 
        hint_text="Buscando dispositivos...",
        on_change=lambda e: page.run_task(on_select_esp32, e)
    )

    esp_status_banner = ft.Container(
        content=ft.Text("ESP32 NO DISPONIBLE", color=ft.Colors.WHITE, weight=ft.FontWeight.BOLD),
        bgcolor=ft.Colors.RED_700,
        padding=10,
        border_radius=5,
        visible=False,
    )

    bulb_icon = ft.Icon(
        name=ft.Icons.LIGHTBULB_OUTLINE,
        size=100,
        color=ft.Colors.GREY_700,
    )

    relay_switch = ft.Switch(
        label="Interruptor de Luz", 
        value=False, 
        disabled=True,
        on_change=lambda e: page.run_task(on_switch_changed, e)
    )

    # --- Helpers UI ---
    def set_ui_state(online: bool):
        nonlocal is_esp_online
        is_esp_online = online
        relay_switch.disabled = not online
        esp_status_banner.visible = (not online) if selected_esp32 else False
        if not online:
            bulb_icon.color = ft.Colors.GREY_700
            relay_switch.value = False
        page.update()

    def refresh_dropdown(items: list[str]):
        nonlocal selected_esp32
        esp32_dropdown.options = [ft.dropdown.Option(x) for x in items]
        
        # Prioridad: Mantener selección -> "esp32_01" -> primero de la lista
        if selected_esp32 not in items:
            if "esp32_01" in items:
                selected_esp32 = "esp32_01"
            else:
                selected_esp32 = items[0] if items else None
            esp32_dropdown.value = selected_esp32
        
        page.update()

    # --------------------------------------------------------------------------
    # Callback de mensajes WS
    # --------------------------------------------------------------------------
    def update_status(data: dict):
        nonlocal selected_esp32

        msg_type = data.get("type")

        if msg_type == "registered":
            status_point.bgcolor = ft.Colors.GREEN_500
            status_text.value = "✅ Servidor Online"
            my_ip = data.get("ip", "desconocida")
            client_ip_text.value = f"Tu IP: {my_ip}"
            page.update()
            return

        if msg_type == "server_disconnected":
            status_point.bgcolor = ft.Colors.RED_500
            status_text.value = "❌ Servidor Offline (Reintentando...)"
            set_ui_state(False)
            return

        if msg_type == "esp32_list":
            items = data.get("items", [])
            old_selection = selected_esp32
            refresh_dropdown(items)
            
            # Si se ha seleccionado algo nuevo automáticamente, pedimos su estado
            if selected_esp32 and selected_esp32 != old_selection:
                page.run_task(ws_client.request_state, selected_esp32)
            return

        if msg_type == "esp32_online":
            new_id = data.get("id")
            old_selection = selected_esp32
            current_items = [o.key for o in esp32_dropdown.options]
            if new_id not in current_items:
                current_items.append(new_id)
                refresh_dropdown(sorted(current_items))
            
            # Si al llegar el nuevo ID se ha seleccionado automáticamente, pedimos estado
            if selected_esp32 == new_id:
                set_ui_state(True)
                if selected_esp32 != old_selection:
                    page.run_task(ws_client.request_state, selected_esp32)
            return

        if msg_type == "esp32_offline":
            off_id = data.get("id")
            current_items = [o.key for o in esp32_dropdown.options if o.key != off_id]
            refresh_dropdown(sorted(current_items))
            
            if selected_esp32 == off_id:
                set_ui_state(False)
            return

        if msg_type == "state":
            from_id = data.get("from")
            if from_id != selected_esp32:
                return

            st = data.get("state")
            set_ui_state(True)

            now = datetime.now().strftime("%H:%M:%S")
            print(f"{now} 💡 Confirmación recibida: {from_id} es {st.upper()}")

            relay_switch.value = (st == "on")
            bulb_icon.name = ft.Icons.LIGHTBULB if st == "on" else ft.Icons.LIGHTBULB_OUTLINE
            bulb_icon.color = ft.Colors.AMBER_500 if st == "on" else ft.Colors.GREY_500
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
        page.update()

    async def on_switch_changed(e):
        if not selected_esp32 or not is_esp_online:
            relay_switch.value = not e.control.value # Revertir
            page.update()
            return

        action = "on" if e.control.value else "off"
        await ws_client.command_relay(selected_esp32, action)

    # --------------------------------------------------------------------------
    # Layout
    # --------------------------------------------------------------------------
    page.add(
        ft.Column(
            [
                title,
                ws_info,
                client_ip_text,
                ft.Divider(),
                server_status_row,
                ft.Divider(),
                esp_status_banner,
                esp32_dropdown,
                ft.Container(bulb_icon, margin=ft.margin.only(top=20, bottom=20)),
                relay_switch,
                ft.Divider(),
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
