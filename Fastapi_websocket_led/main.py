"""
Servidor WebSocket (FastAPI) para controlar relés en múltiples ESP32 desde múltiples frontends.
--------------------------------------------------------------------------------------------

ARQUITECTURA
-----------
    FRONTEND(S) (Flet/web)  <── WS/WSS ──>  FASTAPI SERVER  <── WS/WSS ──>  ESP32(S)

OBJETIVO
--------
- Permitir que uno o varios frontends controlen uno o varios ESP32.
- Los ESP32 se identifican con un ID lógico: "esp32_01", "esp32_02", ...
- El servidor enruta comandos hacia el ESP32 destino usando el campo "to".
- El servidor difunde estados ("state") a todos los frontends conectados.

PROTOCOLO (JSON)
----------------
1) Registro ESP32 (ESP32 -> servidor)   [se envía siempre al conectar/reconectar]
    {
      "type": "register",
      "role": "esp32",
      "id": "esp32_01",
      "mac": "30:C6:..",
      "ip": "192.168.1.42"
    }

2) Registro frontend (frontend -> servidor)
    { "type": "register", "role": "frontend" }

3) Comando (frontend -> servidor -> ESP32)
    { "type":"command", "to":"esp32_01", "device":"relay", "id":0, "action":"on" }

4) Petición de estado al conectar (frontend -> servidor -> ESP32) o respuesta desde cache
    { "type":"get_state", "to":"esp32_01" }

5) Estado (ESP32 -> servidor -> frontends)
    { "type":"state", "from":"esp32_01", "device":"relay", "id":0, "state":"on" }

6) Keep-alive
    ESP32 -> servidor: { "type":"ping", "from":"esp32_01" }
    servidor -> ESP32: { "type":"pong" }

NOTAS IMPORTANTES
----------------
- Este servidor NO decide el estado del relé. Solo enruta y difunde.
- Guardamos el último estado conocido de cada ESP32 (cache) para sincronizar frontends al conectar.
- Para Render: el keep-alive evita que WS se cierre por inactividad.

EJECUCIÓN
---------
Local:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Render:
    Se expone el mismo endpoint /ws, normalmente con WSS en 443 desde fuera.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# --------------------------------------------------------------------------
# 🕒 Timestamp ISO (UTC) tipo ESP32: 2025-12-22T18:01:33Z
# --------------------------------------------------------------------------
def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# --------------------------------------------------------------------------
# 🧩 Helper para identificar conexiones en logs
# --------------------------------------------------------------------------
def peer(ws: WebSocket) -> str:
    try:
        c = ws.client
        if c:
            return f"{c.host}:{c.port}"
    except Exception:
        pass
    return "unknown"

# --------------------------------------------------------------------------
# 🔧 APP FASTAPI + CORS
# --------------------------------------------------------------------------
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],         # En producción puedes restringir dominios
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --------------------------------------------------------------------------
# 🧠 ESTRUCTURAS DE ESTADO EN MEMORIA (simples y efectivas)
# --------------------------------------------------------------------------

# Conexiones activas de ESP32 por id:  {"esp32_01": websocket, ...}
esp32_connections: Dict[str, WebSocket] = {}

# Metadatos conocidos de ESP32 (solo para depurar / validar)
# {"esp32_01": {"mac": "...", "ip": "...", "last_seen": 1234567890.0}}
esp32_meta: Dict[str, Dict[str, Any]] = {}

# Frontends conectados (pueden ser varios a la vez)
frontends: Set[WebSocket] = set()

# Último estado conocido por ESP32 (cache para sincronizar UI)
# Clave (esp32_id, device, id) -> {"type":"state", ...}
state_cache: Dict[Tuple[str, str, int], Dict[str, Any]] = {}

# --------------------------------------------------------------------------
# 🏠 RUTA DE ESTADO (Health Check)
# --------------------------------------------------------------------------
@app.get("/")
async def get_status():
    """Devuelve un resumen del estado del servidor para verificar que esté vivo."""
    return {
        "status": "online",
        "esp32_conectados": list(esp32_connections.keys()),
        "total_frontends": len(frontends),
        "cache_estados": len(state_cache),
        "timestamp": ts(),
    }

# --------------------------------------------------------------------------
# ❤️ KEEP-ALIVE (Render-friendly)
# --------------------------------------------------------------------------
KEEP_ALIVE_SECONDS = 30

async def safe_send_json(ws: WebSocket, payload: Dict[str, Any]) -> bool:
    """Envía JSON y devuelve True si ok; si falla devuelve False."""
    try:
        await ws.send_json(payload)
        return True
    except Exception:
        return False

async def keep_alive_task() -> None:
    """
    Tarea periódica:
    - envía ping JSON a frontends (opcional)
    - limpia frontends caídos
    """
    while True:
        await asyncio.sleep(KEEP_ALIVE_SECONDS)

        dead_fronts = []
        for ws in list(frontends):
            ok = await safe_send_json(ws, {"type": "ping"})
            if not ok:
                dead_fronts.append(ws)

        for ws in dead_fronts:
            frontends.discard(ws)
            print(f"{ts()} 🧹 Frontend caído eliminado (keep-alive) peer={peer(ws)}")

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(keep_alive_task())
    print(f"{ts()} 🚀 Startup: keep_alive_task iniciado (cada {KEEP_ALIVE_SECONDS}s)")

# --------------------------------------------------------------------------
# 🔁 UTILIDADES DE ROUTING
# --------------------------------------------------------------------------
async def broadcast_to_frontends(payload: Dict[str, Any]) -> None:
    """Envía un JSON a todos los frontends conectados (limpiando los caídos)."""
    dead = []
    for ws in list(frontends):
        ok = await safe_send_json(ws, payload)
        if not ok:
            dead.append(ws)

    for ws in dead:
        frontends.discard(ws)
        print(f"{ts()} 🧹 Frontend eliminado (broadcast falló) peer={peer(ws)}")

def cache_state(payload: Dict[str, Any]) -> None:
    """
    Guarda un estado en cache.
    Esperamos formato:
      { "type":"state", "from":"esp32_01", "device":"relay", "id":0, "state":"on" }
    """
    esp32_id = payload.get("from")
    device = payload.get("device")
    dev_id = payload.get("id")

    if isinstance(esp32_id, str) and isinstance(device, str) and isinstance(dev_id, int):
        state_cache[(esp32_id, device, dev_id)] = payload

def get_cached_state_for_esp32(esp32_id: str) -> Optional[Dict[str, Any]]:
    """
    Devuelve un estado "principal" para un esp32 si existe.
    Para esta fase (1 relé), nos vale relay id 0.
    """
    key = (esp32_id, "relay", 0)
    return state_cache.get(key)

# --------------------------------------------------------------------------
# 📡 ENDPOINT WEBSOCKET
# --------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    print(f"{ts()} 🔌 Nueva conexión WebSocket peer={peer(ws)}")

    role: Optional[str] = None
    esp32_id: Optional[str] = None

    try:
        # ----------------------------------------------------------------------
        # 1) PRIMER MENSAJE: register
        # ----------------------------------------------------------------------
        init_msg = await ws.receive_json()

        if init_msg.get("type") != "register":
            await ws.send_json({"type": "error", "message": "Se esperaba type=register en el primer mensaje"})
            await ws.close()
            print(f"{ts()} ❌ Cierre: primer mensaje no fue register peer={peer(ws)} msg={init_msg}")
            return

        role = init_msg.get("role")
        if role not in ("esp32", "frontend"):
            await ws.send_json({"type": "error", "message": "role inválido. Use 'esp32' o 'frontend'"})
            await ws.close()
            print(f"{ts()} ❌ Cierre: role inválido peer={peer(ws)} role={role}")
            return

        # ----------------------------------------------------------------------
        # 1A) REGISTRO DE ESP32
        # ----------------------------------------------------------------------
        if role == "esp32":
            esp32_id = init_msg.get("id")
            if not isinstance(esp32_id, str) or not esp32_id:
                await ws.send_json({"type": "error", "message": "ESP32 register requiere un id válido"})
                await ws.close()
                print(f"{ts()} ❌ Cierre: esp32 id inválido peer={peer(ws)} msg={init_msg}")
                return

            # Guardamos/actualizamos conexión (si había una anterior, se sustituye)
            esp32_connections[esp32_id] = ws

            # Guardamos metadatos útiles (no necesarios para routing, pero sí para depurar)
            esp32_meta[esp32_id] = {
                "mac": init_msg.get("mac"),
                "ip": init_msg.get("ip"),
                "last_seen": time.time(),
            }

            print(f"{ts()} ✅ ESP32 registrado: {esp32_id} | meta={esp32_meta[esp32_id]} | peer={peer(ws)}")

            # Confirmación al ESP32
            await safe_send_json(ws, {"type": "registered", "id": esp32_id})

            # Avisamos a los frontends de que este ESP32 está online
            await broadcast_to_frontends({"type": "esp32_online", "id": esp32_id})

            # Si tenemos cache, lo enviamos al front (no al esp32).
            cached = get_cached_state_for_esp32(esp32_id)
            if cached:
                await broadcast_to_frontends(cached)

        # ----------------------------------------------------------------------
        # 1B) REGISTRO DE FRONTEND
        # ----------------------------------------------------------------------
        if role == "frontend":
            frontends.add(ws)
            print(f"{ts()} ✅ Frontend registrado. Total frontends: {len(frontends)} | peer={peer(ws)}")

            # Confirmación
            await safe_send_json(ws, {"type": "registered", "role": "frontend"})

            # Informar al frontend de qué ESP32 hay conectados ahora mismo
            await safe_send_json(ws, {"type": "esp32_list", "items": sorted(list(esp32_connections.keys()))})

        # ----------------------------------------------------------------------
        # 2) BUCLE PRINCIPAL: recibir y enrutar mensajes
        # ----------------------------------------------------------------------
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            # Actualizamos last_seen si es esp32
            if role == "esp32" and esp32_id:
                esp32_meta.setdefault(esp32_id, {})["last_seen"] = time.time()

            # --------------------------------------------------------------
            # PING/PONG (keep-alive del protocolo JSON)
            # --------------------------------------------------------------
            if msg_type == "ping":
                who = esp32_id if (role == "esp32" and esp32_id) else "frontend"
                # Log claro
                print(f"{ts()} ❤️ PING recibido de {role}:{who} peer={peer(ws)} payload={data}")

                # Respondemos pong (útil para ESP32)
                await safe_send_json(ws, {"type": "pong"})
                print(f"{ts()} 💚 PONG enviado a {role}:{who} peer={peer(ws)}")
                continue

            # --------------------------------------------------------------
            # ESTADO (ESP32 -> servidor -> frontends)
            # --------------------------------------------------------------
            if msg_type == "state":
                cache_state(data)
                print(f"{ts()} 📣 STATE recibido de esp32:{data.get('from')} payload={data}")
                await broadcast_to_frontends(data)
                continue

            # --------------------------------------------------------------
            # COMANDO (frontend -> servidor -> ESP32)
            # --------------------------------------------------------------
            if msg_type == "command":
                if role != "frontend":
                    continue

                print(f"{ts()} 🎮 COMMAND recibido del frontend peer={peer(ws)} payload={data}")

                to_id = data.get("to")
                if not isinstance(to_id, str) or not to_id:
                    await safe_send_json(ws, {"type": "error", "message": "command requiere campo 'to'"})
                    print(f"{ts()} ❌ COMMAND inválido (sin to) peer={peer(ws)} payload={data}")
                    continue

                target_ws = esp32_connections.get(to_id)
                if not target_ws:
                    await safe_send_json(ws, {"type": "error", "message": f"ESP32 destino no conectado: {to_id}"})
                    print(f"{ts()} ❌ COMMAND destino no conectado: {to_id}")
                    continue

                ok = await safe_send_json(target_ws, data)
                print(f"{ts()} ➡️ COMMAND reenviado a esp32:{to_id} ok={ok}")

                if not ok:
                    # Si falló, limpiamos y avisamos
                    esp32_connections.pop(to_id, None)
                    await safe_send_json(ws, {"type": "error", "message": f"No se pudo enviar al ESP32: {to_id}"})
                continue

            # --------------------------------------------------------------
            # GET_STATE (frontend -> servidor -> ESP32, o devolver cache)
            # --------------------------------------------------------------
            if msg_type == "get_state":
                if role != "frontend":
                    continue

                print(f"{ts()} 🔎 GET_STATE recibido del frontend peer={peer(ws)} payload={data}")

                to_id = data.get("to")
                if not isinstance(to_id, str) or not to_id:
                    await safe_send_json(ws, {"type": "error", "message": "get_state requiere campo 'to'"})
                    print(f"{ts()} ❌ GET_STATE inválido (sin to) peer={peer(ws)} payload={data}")
                    continue

                # 1) Si tenemos cache, devolvemos al frontend inmediatamente
                cached = get_cached_state_for_esp32(to_id)
                if cached:
                    await safe_send_json(ws, cached)
                    print(f"{ts()} 🧠 GET_STATE cache enviado a frontend para {to_id}: {cached}")

                # 2) Pedimos estado real al ESP32 si está conectado
                target_ws = esp32_connections.get(to_id)
                if target_ws:
                    ok = await safe_send_json(target_ws, {"type": "get_state", "to": to_id})
                    print(f"{ts()} ➡️ GET_STATE reenviado a esp32:{to_id} ok={ok}")
                else:
                    await safe_send_json(ws, {"type": "warning", "message": f"ESP32 no conectado: {to_id}"})
                    print(f"{ts()} ⚠ GET_STATE: esp32 no conectado: {to_id}")
                continue

            # --------------------------------------------------------------
            # Mensaje desconocido: lo logueamos
            # --------------------------------------------------------------
            print(f"{ts()} ⚠ Mensaje desconocido ({role}) peer={peer(ws)}: {data}")

    except WebSocketDisconnect:
        print(f"{ts()} ❌ Desconexión ({role}) peer={peer(ws)}")

    except Exception as e:
        print(f"{ts()} ⚠ Error inesperado: {e} | role={role} peer={peer(ws)}")

    finally:
        # ----------------------------------------------------------------------
        # Limpieza de la conexión al salir
        # ----------------------------------------------------------------------
        if role == "frontend":
            frontends.discard(ws)
            print(f"{ts()} 🧹 Frontend eliminado. Total frontends: {len(frontends)} peer={peer(ws)}")

        if role == "esp32" and esp32_id:
            # Solo borramos si la conexión almacenada es esta misma (evita borrar reconexiones)
            if esp32_connections.get(esp32_id) == ws:
                esp32_connections.pop(esp32_id, None)
                print(f"{ts()} 🧹 ESP32 eliminado: {esp32_id} peer={peer(ws)}")
                await broadcast_to_frontends({"type": "esp32_offline", "id": esp32_id})

# --------------------------------------------------------------------------
# 🏁 EJECUCIÓN LOCAL
# --------------------------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
