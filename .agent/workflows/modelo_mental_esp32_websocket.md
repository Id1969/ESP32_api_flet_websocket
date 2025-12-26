---
description: Modelo Mental Compartido - Arquitectura WebSocket Reactiva (ESP32-Backend-Front)
---

Este documento define el estándar de arquitectura para el proyecto ESP32 WebSocket, diseñado para ser ligero, robusto y libre de bucles de saturación.

## 🔌 Principios de Conexión
1. **WebSocket por Entidad**: 1 WS por cada ESP32 y 1 WS por cada instancia de Frontend.
2. **Backend Centralizado**: Toda comunicación pasa por el Backend. No hay tráfico ESP32 ↔ Front directo.
3. **Cero Pings de Aplicación**: Se eliminan los pings manuales (heartbeats JSON). Se confía en la salud del socket TCP y en la "Purga por Intento de Uso".

## 🧩 Responsabilidades Claras

### ESP32 (El Cliente Silencioso)
- **Inicio**: Al conectar, envía `register` y su `state` actual **una sola vez**.
- **Silencio**: NO envía pings periódicos.
- **Reactividad**: Solo habla cuando recibe un `command` o un `get_state`.
- **Resiliencia**: Si cae la conexión, la librería reintenta conectar en silencio (cada 5s). Solo "grita" de nuevo cuando logra entrar.

### Backend (El Notario y Distribuidor)
- **Registro**: Mantiene un diccionario de sockets vivos (`esp32_connections`).
- **Evento Online**: Al recibir un `register`, notifica a los frentes con `esp32_online`.
- **Purga Inmediata**: Si un envío de comando falla, o se detecta desconexión:
    1. Elimina al ESP32 del registro.
    2. Notifica a los frentes con `esp32_offline`.
    3. NO intenta reconectar al ESP32.
- **Snapshot inicial**: Envía la lista completa al Frontend cuando este conecta por primera vez.

### Frontend (La UI Reactiva)
- **Sincronización Incremental**: Escucha eventos `esp32_online` y `esp32_offline` para actualizar su dropdown (Suma/Resta elementos).
- **Cero Polling**: No pregunta periódicamente.
- **Demanda Única**: Solo pide el estado (`get_state`) cuando el usuario selecciona un ESP32 o al recibir un `online` del dispositivo seleccionado.
- **Bloqueo**: Si recibe `esp32_offline`, deshabilita inmediatamente sus controles.

## 🔁 Ciclo de Vida Limpio

1. **Aparición**: ESP32 conecta -> Register -> Backend avisa `online` -> Front suma a Dropdown.
2. **Operación**: Front envía `command` -> Backend relaya -> ESP32 ejecuta y responde `state`.
3. **Desaparición**: ESP32 cae -> Backend detecta/falla envío -> Backend borra y avisa `offline` -> Front borra de Dropdown y bloquea.
4. **Fin**: Nadie hace nada más hasta que el ESP32 reaparezca por su cuenta.

---
*No bucles. No reintentos desde el servidor. No pings innecesarios.*
