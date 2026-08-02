# Bolsa Virtual Pro

Simulador de inversión en bolsa con dinero ficticio. Cuentas sin contraseña
(solo nombre), mercado de 10 empresas ficticias, compra/venta con lógica
100% en el servidor, panel de administración oculto y simulación automática
del mercado.

## Tecnologías

- **Backend:** Python 3 + Flask
- **Base de datos:** SQLite (archivo `bolsa.db`, se crea solo)
- **Frontend:** HTML + CSS + JavaScript puro (sin frameworks)
- **Gráficos:** Chart.js (incluido localmente en `static/js/vendor/`, sin CDN)
- **Sin servicios externos:** todo funciona 100% offline / local

## Estructura del proyecto

```
bolsa_virtual_pro/
├── app.py                  # Servidor Flask: páginas + API REST + lógica de negocio
├── database.py              # Esquema SQLite, conexión y datos semilla
├── requirements.txt         # Dependencias de Python
├── bolsa.db                  # Se crea automáticamente al ejecutar la app
├── templates/
│   ├── index.html            # Login + dashboard del usuario (SPA)
│   └── admin.html            # Panel de administración (ruta /admin)
└── static/
    ├── css/style.css         # Sistema de diseño (tema oscuro/claro)
    └── js/
        ├── app.js            # Utilidades compartidas (toasts, tema, sonido)
        ├── dashboard.js       # Lógica del panel de usuario
        ├── admin.js           # Lógica del panel de administración
        └── vendor/chart.umd.min.js
```

## Instalación y ejecución local

1. Requisitos: Python 3.9 o superior.
2. Instalar dependencias:

   ```bash
   pip install -r requirements.txt
   ```

3. Ejecutar el servidor:

   ```bash
   python app.py
   ```

4. Abrir el navegador en:

   - Aplicación de usuario: **http://127.0.0.1:5000/**
   - Panel de administración: **http://127.0.0.1:5000/admin**
     - Usuario: `admin`
     - Contraseña: `123`

La base de datos `bolsa.db` se crea y se siembra automáticamente la primera
vez que se ejecuta `app.py` (10 empresas ficticias y configuración inicial).

## Cómo funciona

### Cuentas de usuario
Se piden **usuario y contraseña** (mínimo 4 caracteres). Si el usuario no
existe, la cuenta se crea automáticamente con esa contraseña y **$0** de
saldo inicial; si ya existe, se valida la contraseña contra el hash
guardado (nunca se guarda en texto plano). El administrador también
puede crear cuentas manualmente desde `/admin` y fijarles una contraseña
y un saldo inicial distinto si lo desea.

### Depósitos y retiros de dinero ficticio
Los usuarios pueden **solicitar** que se les agregue o se les retire dinero
ficticio desde el panel principal (pestañas "Solicitar depósito" /
"Solicitar retiro"), pero el saldo **no cambia de inmediato**: cada
solicitud queda pendiente hasta que el administrador la apruebe o la
rechace desde la pestaña "Solicitudes de dinero" de `/admin`. Al aprobar un
retiro, el sistema revalida que el usuario siga teniendo saldo suficiente
(por si compró acciones mientras esperaba). El administrador conserva
además su control directo de agregar/quitar dinero a cualquier cuenta sin
pasar por este flujo.

### Simulación automática con oferta y demanda real
Cuando activas "Iniciar mercado automático" desde `/admin`, cada **10
segundos** el precio de cada acción se recalcula combinando dos fuerzas:
1. **Oferta y demanda real:** si los usuarios compraron más de lo que
   vendieron desde el último ciclo, el precio sube; si vendieron más de lo
   que compraron, el precio baja (hasta ±8% por este efecto).
2. **Ruido de mercado aleatorio** (±2%) para simular la volatilidad normal
   de cualquier bolsa, incluso sin actividad de los usuarios.

El resultado combinado siempre queda limitado entre -10% y +10% por ciclo.
Este comportamiento vive en `market_loop()` dentro de `app.py`, junto con
`register_trade_volume()` (que registra cada compra/venta) y la constante
`MARKET_LIQUIDITY` (que controla qué tan sensible es el precio a la
actividad de los usuarios — bájala para que el mercado reaccione más
fuerte, súbela para que reaccione más suave).

### Acciones más accesibles y compras fraccionarias
Los precios iniciales de las 10 empresas ficticias se redujeron (entre ~$6
y ~$65) para que sean más accesibles. Además, se puede comprar y vender
**cantidades fraccionarias** de cualquier acción (0.1, 0.5, 2.25, etc.): el
campo de cantidad acepta decimales tanto en el mercado como en el modal de
compra/venta, y el backend valida y guarda esas fracciones con precisión.

### Mercado
10 empresas ficticias (Apple, Microsoft, Tesla, Amazon, Google, Nvidia,
Meta, Coca-Cola, Netflix, Samsung), cada una con precio, variación %,
historial de precios y gráfico en tiempo real.

### Compra / venta
Toda la validación (saldo suficiente, cantidades positivas, acciones
disponibles en cartera) ocurre en el servidor. El navegador nunca decide
saldos ni resultados.

### Panel de administración (`/admin`)
Permite gestionar usuarios (editar, bloquear, eliminar, reiniciar, dar/quitar
dinero y acciones) y el mercado (editar cualquier acción, botones rápidos de
±1/3/5/10%, porcentaje manual, y arrancar/detener la simulación automática
que mueve todos los precios cada 5 segundos entre -10% y +10%).

### Exportación
Desde "Historial" el usuario puede exportar sus operaciones a PDF o Excel.

## Notas de seguridad

- El saldo nunca puede quedar negativo.
- No se puede comprar sin saldo suficiente ni vender acciones que no se
  tienen.
- Toda la lógica de negocio vive en `app.py`; el frontend solo la consume
  mediante la API REST y no puede alterar resultados.
- Las credenciales de administrador están fijas según la especificación
  del proyecto (`admin` / `123`). Para un entorno real, se recomienda
  cambiarlas y usar variables de entorno.

## Compartir con un link rápido (sin desplegar, solo para probar)

Si quieres que alguien pruebe la app AHORA MISMO desde tu propio computador
(sin subirla a ningún hosting todavía), puedes usar un túnel temporal:

1. Corre la app normalmente (`python app.py` o con el lanzador de doble
   clic).
2. Descarga **ngrok** (gratis) desde https://ngrok.com/download e instálalo
   siguiendo sus instrucciones (requiere crear una cuenta gratuita).
3. En otra terminal, ejecuta:
   ```bash
   ngrok http 5000
   ```
4. ngrok te da un link público como `https://algo-random.ngrok-free.app`
   que reenvía tráfico a tu app local. Compártelo con quien quieras.

⚠️ Este link es temporal: solo funciona mientras tu computador y `app.py`
sigan corriendo, y cambia cada vez que reinicias ngrok (en el plan
gratuito). Para un link permanente, usa la sección de despliegue de abajo.

## Desplegar en internet (producción, link permanente)

El proyecto ya incluye `gunicorn` (servidor de producción) y un `Procfile`
listo para plataformas como Render o Railway.

### Variables de entorno recomendadas en producción
| Variable | Para qué sirve | Valor por defecto |
|---|---|---|
| `SECRET_KEY` | Firma las sesiones de Flask. Usa un valor largo y aleatorio. | clave de desarrollo (cámbiala) |
| `ADMIN_USER` | Usuario del panel `/admin`. | `admin` |
| `ADMIN_PASS` | Contraseña del panel `/admin`. | `123` |
| `PORT` | Puerto donde escucha el servidor (la mayoría de plataformas lo define solas). | `5000` |

### Opción A — PythonAnywhere (recomendada para empezar, sin usar Git)
1. Crea una cuenta gratis en pythonanywhere.com.
2. Sube el `.zip` del proyecto desde la pestaña **Files** y descomprímelo
   con la consola Bash que te dan.
3. Ve a **Web → Add a new web app → Flask**, apunta al archivo `app.py`.
4. En una consola Bash: `pip install -r requirements.txt`.
5. Configura las variables de entorno (`SECRET_KEY`, `ADMIN_USER`,
   `ADMIN_PASS`) desde la pestaña **Web → Environment variables**.
6. Dale **Reload**. Tu app queda en `tunombre.pythonanywhere.com`.
7. Ventaja: el disco es persistente, así que `bolsa.db` no se borra.

### Opción B — Render.com (requiere GitHub)
1. Sube el proyecto a un repositorio de GitHub.
2. En Render: **New → Web Service** → conecta el repositorio.
3. Comando de build: `pip install -r requirements.txt`.
4. Comando de arranque: usa el `Procfile` incluido (Render lo detecta solo)
   o define manualmente `gunicorn app:app --workers 1 --threads 4 --bind 0.0.0.0:$PORT`.
5. Agrega las variables de entorno `SECRET_KEY`, `ADMIN_USER`, `ADMIN_PASS`.
6. ⚠️ El disco de Render es efímero en el plan gratuito: `bolsa.db` se
   reinicia en cada despliegue. Para datos persistentes ahí, agrega un
   **Persistent Disk** de Render o migra a PostgreSQL (ver siguiente
   sección).

**Importante:** usa siempre `--workers 1` (ya viene así en el `Procfile`).
El simulador de mercado automático corre en un hilo dentro del proceso; con
más de un worker se duplicaría en cada uno.

## Migrar a MySQL (opcional)

Toda la capa de datos vive en `database.py`. Para migrar a MySQL basta con
reemplazar `get_connection()` por una conexión con `mysql-connector-python`
(o `PyMySQL`) y ajustar los tipos `AUTOINCREMENT` → `AUTO_INCREMENT`; el
resto de las consultas usa SQL estándar compatible.
