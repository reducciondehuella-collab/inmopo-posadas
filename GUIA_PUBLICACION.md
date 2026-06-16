# 🏡 GUÍA COMPLETA — InmoPosadas
## Cómo poner tu web en línea y administrarla
### Sin conocimientos de programación · Todo gratuito

---

> **¿Qué vas a tener al final de esta guía?**
> Una página web real en internet, con una dirección como `https://inmopo-posadas.onrender.com`,
> que cualquier persona puede visitar desde su celular o computadora, con el mapa de inmuebles
> de Posadas actualizado automáticamente cada 6 horas.

---

## 📋 RESUMEN RÁPIDO

| Servicio | Para qué sirve | Costo |
|---|---|---|
| **GitHub** | Guardar el código de la web | Gratis |
| **Render** | Ejecutar la web en internet | Gratis |
| **UptimeRobot** | Mantenerla despierta 24/7 | Gratis |

**Tiempo total estimado: 30-40 minutos la primera vez.**

---

## 🗂️ PARTE 1 — CREAR TU CUENTA EN GITHUB

GitHub es como una "carpeta en la nube" donde guardás el código de tu web.

### Paso 1: Registrarse
1. Abrí tu navegador y entrá a **https://github.com**
2. Hacé clic en el botón verde **"Sign up"** (arriba a la derecha)
3. Completá:
   - **Username:** elegí un nombre de usuario (ej: `inmopo-posadas`)
   - **Email:** tu correo electrónico
   - **Password:** una contraseña segura
4. Verificá tu cuenta con el código que te llega al email
5. Cuando pregunte "What kind of work do you do?", podés seleccionar
   cualquier opción y hacer clic en **"Continue"**
6. En el plan, elegí **"Continue for free"**

✅ **Ya tenés tu cuenta de GitHub.**

---

### Paso 2: Subir el código (MÉTODO FÁCIL — sin instalar nada)

1. Entrá a tu cuenta de GitHub
2. Hacé clic en el botón **"+"** (arriba a la derecha) → **"New repository"**
3. Completá:
   - **Repository name:** `inmopo-posadas`
   - **Description:** `Mapa inmobiliario de Posadas, Misiones`
   - Seleccioná **"Public"** (público)
   - ✅ Marcá **"Add a README file"**
4. Hacé clic en **"Create repository"**

Ahora vas a ver tu repositorio vacío. Tenés que subir los archivos:

5. Hacé clic en **"uploading an existing file"** (en el centro de la pantalla)
   o en **"Add file" → "Upload files"**
6. Abrí la carpeta `inmopo` que descargaste en tu computadora
7. Seleccioná TODOS los archivos y carpetas de adentro y arrastralos
   a la ventana del navegador:
   - Carpeta `backend/`
   - Carpeta `frontend/`
   - Archivo `Dockerfile`
   - Archivo `render.yaml`
   - Archivo `.gitignore`
   - Archivos `iniciar_windows.bat` e `iniciar_linux_mac.sh`

   ⚠️ **NO subas la carpeta `data/`** (si existe)

8. Abajo de la pantalla, en "Commit changes", escribí: `Primera versión`
9. Hacé clic en **"Commit changes"** (botón verde)

✅ **Tu código ya está en GitHub.**

---

## 🌐 PARTE 2 — PUBLICAR EN RENDER

Render es el servicio que va a ejecutar tu web en internet, gratis.

### Paso 3: Crear cuenta en Render

1. Entrá a **https://render.com**
2. Hacé clic en **"Get Started for Free"**
3. Hacé clic en **"GitHub"** para registrarte con tu cuenta de GitHub
   (es la forma más fácil — no necesitás crear otra contraseña)
4. GitHub te va a pedir que autorices a Render. Hacé clic en **"Authorize Render"**
5. Completá los datos que pida (nombre, etc.)

✅ **Ya tenés tu cuenta de Render.**

---

### Paso 4: Crear el servicio web

1. En el panel de Render, hacé clic en **"New +"** (arriba a la derecha)
2. Seleccioná **"Web Service"**
3. En la siguiente pantalla, en la sección **"Connect a repository"**,
   buscá tu repositorio `inmopo-posadas` y hacé clic en **"Connect"**
4. Completá el formulario así:

   | Campo | Valor |
   |---|---|
   | **Name** | `inmopo-posadas` |
   | **Region** | `Ohio (US East)` (o el que esté seleccionado) |
   | **Branch** | `main` |
   | **Runtime** | `Python 3` |
   | **Build Command** | `pip install -r backend/requirements.txt` |
   | **Start Command** | `uvicorn backend.main:app --host 0.0.0.0 --port $PORT` |
   | **Instance Type** | `Free` ← ¡Importante! |

5. Hacé clic en **"Create Web Service"**

6. Render va a empezar a construir tu web. Vas a ver logs (textos)
   que aparecen en la pantalla. **Esperá 3-5 minutos.**

7. Cuando veas `=== Scraping completo: NNN propiedades ===` en los logs,
   ¡tu web ya está funcionando!

8. En la parte superior de la pantalla vas a ver una URL como:
   **`https://inmopo-posadas.onrender.com`**

   Hacé clic ahí para abrir tu web. 🎉

✅ **Tu web está en línea.**

---

## ⏰ PARTE 3 — MANTENERLA DESPIERTA (MUY IMPORTANTE)

Render en plan gratuito "duerme" la web si no hay visitas en 15 minutos.
Cuando alguien entra y la web está dormida, tarda 30-60 segundos en despertar.
Para evitar esto, usamos **UptimeRobot** que "toca el timbre" cada 5 minutos.

### Paso 5: Configurar UptimeRobot

1. Entrá a **https://uptimerobot.com**
2. Hacé clic en **"Register for FREE"**
3. Completá con tu email y contraseña
4. Verificá tu cuenta desde el email que te llegó
5. Una vez adentro, hacé clic en **"+ Add New Monitor"**
6. Completá así:

   | Campo | Valor |
   |---|---|
   | **Monitor Type** | `HTTP(s)` |
   | **Friendly Name** | `InmoPosadas` |
   | **URL** | `https://inmopo-posadas.onrender.com/health` |
   | **Monitoring Interval** | `5 minutes` |

   *(Reemplazá la URL con la tuya exacta de Render)*

7. Hacé clic en **"Create Monitor"**

✅ **Tu web va a estar activa las 24 horas, los 7 días de la semana.**

---

## 📊 PARTE 4 — ADMINISTRAR TU WEB

### ¿Cómo saber si todo funciona?

Entrá a: `https://TU-URL.onrender.com/api/status`

Vas a ver algo como:
```
{
  "scraping_en_curso": false,
  "ultima_actualizacion": "2025-06-07T14:30:00",
  "total_propiedades": 847,
  "datos_disponibles": true
}
```

Si `datos_disponibles` es `true` y `total_propiedades` tiene un número grande
(más de 100), todo funciona perfectamente.

---

### ¿Cómo actualizar los datos manualmente?

Si querés forzar una actualización inmediata (sin esperar las 6 horas):

1. Abrí tu web en el navegador
2. En el encabezado verde, hacé clic en el botón **"🔄 Actualizar"**
3. El sistema va a buscar nuevas propiedades. Tarda 2-5 minutos.

O podés entrar directamente a:
`https://TU-URL.onrender.com/api/refresh` (necesitás enviar una solicitud POST,
lo más fácil es hacerlo desde el botón de la web)

---

### ¿Cómo ver los logs (registros de actividad)?

1. Entrá a **https://render.com** con tu cuenta
2. Hacé clic en tu servicio `inmopo-posadas`
3. En el menú de la izquierda, hacé clic en **"Logs"**
4. Ahí vas a ver todo lo que hace el servidor, incluido el scraping

---

### ¿Cómo ver si la web está caída?

UptimeRobot te va a enviar un **email automático** si tu web se cae.
También podés ver el historial en `https://uptimerobot.com`

---

## 🔧 PARTE 5 — ACTUALIZACIONES Y CAMBIOS

### Actualizar el código (si modificás algo)

Si necesitás hacer algún cambio en el código:

1. Hacé los cambios en los archivos de tu computadora
2. Entrá a tu repositorio en GitHub:
   `https://github.com/TU-USUARIO/inmopo-posadas`
3. Abrí el archivo que querés cambiar, hacé clic en el ícono del lápiz ✏️
4. Hacé los cambios y hacé clic en **"Commit changes"**
5. Render va a detectar el cambio y **actualizar la web automáticamente**
   en 2-3 minutos (sin que hagas nada más)

---

### ¿Qué hacer si la web muestra "Servidor no disponible"?

1. Entrá a Render y revisá los logs
2. Si ves un error rojo, copiá el texto y buscalo en Google
3. Podés hacer clic en **"Manual Deploy"** → **"Deploy latest commit"**
   para reiniciar el servicio

---

### ¿Qué hacer si el scraping trae pocos datos?

Esto puede pasar si los sitios web cambiaron su estructura. Para solucionarlo:

1. Revisá los logs en Render para ver qué fuente falló
2. Contactá a quien desarrolló el sistema para que ajuste el scraper
3. Mientras tanto, los datos de MercadoLibre (API oficial) siempre van a funcionar

---

## 📱 PARTE 6 — CÓMO USAR LA WEB

### Para los visitantes

1. **Abrir el mapa:** Entrá a `https://TU-URL.onrender.com`
2. **Filtrar:** Usá los selectores de la izquierda (tipo, barrio, precio, etc.)
3. **Ver detalles:** Hacé clic en cualquier marcador del mapa o tarjeta de la lista
4. **Contactar:** En el detalle de cada propiedad, completá tu nombre y email
   y hacé clic en "Enviar consulta" o "WhatsApp"

### Para vos como administrador

- **URL de tu web:** `https://inmopo-posadas.onrender.com`
- **Panel de Render:** `https://render.com` (para ver logs y reiniciar)
- **Panel de UptimeRobot:** `https://uptimerobot.com` (para ver si está activa)
- **Estado de la API:** `https://TU-URL.onrender.com/api/status`

---

## 💰 RESUMEN DE COSTOS

| Qué | Costo |
|---|---|
| GitHub | **GRATIS** (ilimitado) |
| Render (plan Free) | **GRATIS** (750 hs/mes = todo el mes) |
| UptimeRobot | **GRATIS** (hasta 50 monitores) |
| **TOTAL** | **$0 por mes** |

---

## ❓ PREGUNTAS FRECUENTES

**¿Cuándo se actualizan los datos?**
Automáticamente cada 6 horas. También podés forzarlo con el botón "Actualizar".

**¿Puede verse desde el celular?**
Sí, el diseño es responsive y funciona en cualquier dispositivo.

**¿Qué pasa si Render cambia sus condiciones?**
El plan gratuito de Render lleva años estable. Si algún día lo discontinúan,
hay alternativas gratuitas similares: Railway, Fly.io, o Koyeb.

**¿Puedo poner mi propio dominio (ej: mapainmobiliarioposadas.com.ar)?**
Sí, pero el dominio hay que comprarlo (~$15/año). Render permite configurarlo
gratis en cualquier plan.

**¿Cuántas personas pueden usar la web al mismo tiempo?**
En el plan gratuito de Render, hasta ~50 usuarios simultáneos sin problema.
Para más usuarios, el plan Starter cuesta $7/mes.

**¿Los datos son exactos?**
Los datos vienen directamente de MercadoLibre (API oficial), ZonaProp,
Argenprop e inmobiliarias locales. Son tan exactos como los publicó cada inmobiliaria.

---

## 📞 SOPORTE RÁPIDO

Si algo no funciona, estos son los pasos a seguir en orden:

1. **Refrescá la página** del navegador (Ctrl+F5 o Cmd+R)
2. **Esperá 2 minutos** — a veces el servidor tarda en despertar
3. **Revisá los logs** en Render para ver si hay un error
4. **Reiniciá el servicio** en Render: "Manual Deploy" → "Deploy latest commit"
5. Si el problema persiste, anotá el error que aparece en los logs

---

*InmoPosadas · Posadas, Misiones · Sistema de mapa inmobiliario en tiempo real*
