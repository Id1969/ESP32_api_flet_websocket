📡 Proyecto IoT: Control de dispositivos con ESP32, FastAPI y Flet
📝 Descripción general

Este proyecto implementa una arquitectura IoT completa que permite encender y apagar dispositivos eléctricos (LEDs, relés, etc.) conectados a un ESP32 desde una interfaz gráfica desarrollada en Python con Flet, utilizando un servidor WebSocket basado en FastAPI como intermediario de comunicación en tiempo real.

El sistema está diseñado para ser bidireccional, escalable y extensible, permitiendo no solo el control remoto de actuadores, sino también la monitorización del estado de los dispositivos conectados.

🧩 Arquitectura del sistema
┌──────────────┐        WebSocket        ┌────────────────────┐        WiFi        ┌───────────────┐
│  Frontend    │ <────────────────────> │  Backend FastAPI   │ <───────────────> │    ESP32      │
│  (Flet UI)   │                        │  Servidor WS       │                  │  + Relé / LED │
└──────────────┘                        └────────────────────┘                  └───────────────┘

⚙️ Componentes del proyecto
🔹 ESP32

Conectado a la red WiFi.

Establece una conexión WebSocket con el servidor.

Recibe comandos de encendido/apagado.

Actúa sobre un relé (o LED de prueba).

Envía su estado actual al servidor.

🔹 Backend – FastAPI + WebSocket

Gestiona todas las conexiones WebSocket.

Identifica clientes (ESP32 y frontend).

Reenvía comandos del frontend al ESP32 correspondiente.

Reenvía estados y confirmaciones del ESP32 al frontend.

Preparado para despliegue en local o en la nube (Render, VPS, etc.).

Soporta variables de entorno mediante .env.

🔹 Frontend – Flet (Python)

Interfaz gráfica multiplataforma.

Botones para encender y apagar dispositivos.

Visualización en tiempo real del estado del relé.

Comunicación directa con el backend mediante WebSocket.

Pensado para ampliarse con más dispositivos o sensores.

🔁 Flujo de funcionamiento

El servidor FastAPI se inicia y expone un endpoint WebSocket.

El ESP32 se conecta al servidor y se registra como dispositivo IoT.

El frontend Flet se conecta al mismo servidor como cliente de control.

El usuario pulsa un botón en la interfaz.

El comando se envía por WebSocket al servidor.

El servidor reenvía la orden al ESP32.

El ESP32 acciona el relé (ON / OFF).

El ESP32 envía su estado actualizado.

El frontend refleja el cambio en tiempo real.

🧪 Uso de relé

Aunque el ejemplo puede utilizar un LED para pruebas, el proyecto está pensado para controlar relés, permitiendo:

Encender/apagar lámparas

Controlar enchufes

Activar dispositivos de baja tensión

Integrarse en sistemas domóticos

⚠️ Nota de seguridad:
Si se controla corriente alterna (220V), es obligatorio usar relés adecuados, aislamiento correcto y seguir normas eléctricas.