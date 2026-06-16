# 🏡 InmoPosadas — Mapa Inmobiliario en Tiempo Real
### Posadas, Misiones · Sistema completo de scraping + mapa interactivo

---

## ¿Qué hace este sistema?

Obtiene **automáticamente y de forma gratuita** todos los inmuebles en venta
en Posadas desde múltiples fuentes:

| Fuente | Tipo | Propiedades aprox. |
|---|---|---|
| **MercadoLibre** | API pública oficial | 800-1.200 |
| **ZonaProp** | Scraping HTML | 300-700 |
| **Argenprop** | Scraping HTML | 200-400 |
| **Fénix Inmobiliaria** | Scraping local | 50-100 |
| **Costa Remates** | Scraping local | 20-50 |
| **Origen Propiedades** | Scraping local | 20-50 |
| **Singles Inmobiliaria** | Scraping local | 20-50 |
| **Enlaces Bienes Raíces** | Scraping local | 20-50 |

---

## Requisitos

- **Python 3.10+** instalado
- Conexión a internet
- Navegador web moderno

---

## Instalación y arranque

### Windows
```
Doble clic en: iniciar_windows.bat
```

### Linux / Mac
```bash
chmod +x iniciar_linux_mac.sh
./iniciar_linux_mac.sh
```

### Manual (cualquier OS)
```bash
# Desde la carpeta raíz del proyecto:
pip install fastapi uvicorn httpx aiofiles python-dotenv
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

Luego abrir en el navegador: **http://localhost:8000**

---

## Primer uso

1. Al iniciar, el servidor detecta que no hay datos y lanza el scraping automáticamente.
2. El banner muestra el progreso en tiempo real.
3. El primer scraping tarda **2-5 minutos** según la velocidad de internet.
4. Los datos se guardan en `data/propiedades.json` y se actualizan cada 6 horas.
5. Para forzar una actualización: botón **🔄 Actualizar** en la web, o POST a `/api/refresh`.

---

## Estructura del proyecto

```
inmopo/
├── backend/
│   ├── main.py              ← Servidor FastAPI + todos los scrapers
│   └── requirements.txt     ← Dependencias Python
├── frontend/
│   └── index.html           ← Mapa interactivo (se sirve desde el backend)
├── data/
│   ├── propiedades.json     ← Base de datos local (generada automáticamente)
│   └── cache_meta.json      ← Metadata de última actualización
├── iniciar_windows.bat
├── iniciar_linux_mac.sh
└── README.md
```

---

## API REST disponible

| Endpoint | Método | Descripción |
|---|---|---|
| `/api/propiedades` | GET | Lista paginada con filtros |
| `/api/estadisticas` | GET | Resumen estadístico |
| `/api/status` | GET | Estado del servidor y scraping |
| `/api/refresh` | POST | Forzar re-scraping |

### Parámetros de filtro (`/api/propiedades`):
- `tipo=Casa|Departamento|Terreno|Local|Duplex|PH`
- `barrio=Centro`
- `moneda=USD|ARS`
- `precio_min=50000&precio_max=200000`
- `dormitorios=3`
- `fuente=MercadoLibre`
- `limit=100&offset=0`

### Ejemplo:
```
GET http://localhost:8000/api/propiedades?tipo=Casa&moneda=USD&precio_max=150000&limit=50
```

---

## Configuración avanzada

Editá las constantes al inicio de `backend/main.py`:

```python
REFRESH_HOURS = 6      # Cada cuántas horas re-scrapear
MAX_PAGES     = 10     # Máximo de páginas por fuente
REQUEST_DELAY = 1.2    # Segundos entre requests (NO bajar de 1.0)
```

---

## Agregar nuevas inmobiliarias locales

En `backend/main.py`, función `scrape_inmobiliarias_locales()`,
agregá un dict al array `fuentes`:

```python
{
    "nombre":   "Nueva Inmobiliaria",
    "url_base": "https://nuevainmobiliaria.com.ar/venta",
    "email":    "info@nuevainmobiliaria.com.ar",
    "tel":      "+5493764XXXXXX",
},
```

El parser genérico intentará extraer precios y títulos automáticamente.
Para sitios más complejos, podés agregar un scraper específico siguiendo
el patrón de `scrape_zonaprop()`.

---

## Geocodificación

Las propiedades que **ya traen coordenadas** (MercadoLibre) se muestran exactas.
Las que no (ZonaProp, Argenprop, locales) se ubican por **barrio** usando la
tabla `BARRIOS_COORDS` en `main.py`. Podés agregar más barrios:

```python
BARRIOS_COORDS = {
    ...
    "nuevo barrio": (-27.XXXX, -55.XXXX),
}
```

---

## ⚖️ Consideraciones legales

- Los datos de **MercadoLibre** se obtienen vía su **API pública oficial** (sin auth requerida para lectura).
- El scraping de otros sitios se hace con **delays respetuosos** (≥1.2s entre requests).
- Este sistema es para **uso personal / investigación de mercado**. No redistribuyas los datos.
- Respeta los `robots.txt` y términos de uso de cada sitio.

---

## Soporte

Sistema desarrollado para mapeo inmobiliario de Posadas, Misiones, Argentina.
Contacto del propietario del sistema: configurar en el frontend.
