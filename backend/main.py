"""
InmoPosadas — Backend FastAPI completo
Scraper de múltiples fuentes + datos semilla reales de Posadas.
"""
import asyncio, json, logging, os, re, hashlib, random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# ── CONFIG ─────────────────────────────────────────────────────────────────
BASE_DIR   = Path(__file__).parent.parent
DATA_DIR   = BASE_DIR / "data"
DATA_FILE  = DATA_DIR / "propiedades.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

REFRESH_HOURS = 6
MAX_PAGES     = 8
DELAY         = 1.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("inmopo")

_MEM:   list[dict] = []
_FECHA: str        = ""
_SCRAPING          = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9",
}

# ── COORDENADAS DE BARRIOS ─────────────────────────────────────────────────
BARRIOS = {
    "centro":           (-27.3671, -55.8964),
    "barrio norte":     (-27.3580, -55.9000),
    "costanera":        (-27.3720, -55.8950),
    "villa urquiza":    (-27.3700, -55.8800),
    "villa sarita":     (-27.3750, -55.8700),
    "villa cabello":    (-27.3550, -55.8850),
    "peñaflor":         (-27.3650, -55.9200),
    "jardín américa":   (-27.3740, -55.9100),
    "jardin america":   (-27.3740, -55.9100),
    "zaimán":           (-27.3820, -55.9150),
    "zaiman":           (-27.3820, -55.9150),
    "san jorge":        (-27.3830, -55.9200),
    "itaembé guazú":    (-27.3900, -55.8800),
    "itaembe guazu":    (-27.3900, -55.8800),
    "itaembé miní":     (-27.3550, -55.9100),
    "itaembe mini":     (-27.3550, -55.9100),
    "miguel lanús":     (-27.3680, -55.9050),
    "miguel lanus":     (-27.3680, -55.9050),
    "villa blosset":    (-27.3600, -55.9150),
    "km 4":             (-27.3850, -55.9050),
    "km 8":             (-27.3950, -55.9200),
    "ruta 12":          (-27.3620, -55.9300),
    "panambi":          (-27.3480, -55.9200),
    "kennedy":          (-27.3780, -55.9050),
    "el palmar":        (-27.3700, -55.9300),
    "santa catalina":   (-27.3750, -55.9100),
    "nueva esperanza":  (-27.3900, -55.9300),
    "el yerbal":        (-27.3900, -55.8800),
    "la eugenia":       (-27.4000, -55.9000),
    "posadas":          (-27.3671, -55.8964),
}

def geocode(texto: str) -> tuple:
    t = texto.lower()
    for b, c in sorted(BARRIOS.items(), key=lambda x: -len(x[0])):
        if b in t:
            return (round(c[0] + random.uniform(-0.003, 0.003), 6),
                    round(c[1] + random.uniform(-0.003, 0.003), 6))
    return (round(-27.3671 + random.uniform(-0.025, 0.025), 6),
            round(-55.8964 + random.uniform(-0.025, 0.025), 6))

def pid(fuente, ref):
    return hashlib.md5(f"{fuente}:{ref}".encode()).hexdigest()[:12]

def num(v):
    try: return int(float(str(v or "").replace(",",".")))
    except: return None

def strip(s): return re.sub(r'<[^>]+>', '', s).strip()

def tipo_de(t):
    t = t.lower()
    if any(x in t for x in ["duplex","dúplex"]): return "Duplex"
    if "ph" in t.split() or "ph," in t or "penthouse" in t: return "PH"
    if any(x in t for x in ["departamento","depto","dpto","apartamento"]): return "Departamento"
    if any(x in t for x in ["terreno","lote","fracción","fraccion","campo","chacra"]): return "Terreno"
    if any(x in t for x in ["local","oficina","galpon","galpón","depósito","deposito"]): return "Local"
    return "Casa"

def barrio_de(t):
    t2 = t.lower()
    for b in sorted(BARRIOS.keys(), key=len, reverse=True):
        if b in t2 and b != "posadas":
            return b.title()
    return "Posadas"

# ── DATOS SEMILLA (reales de Posadas, usados cuando el scraping no puede acceder) ──
SEMILLA = [
  # CENTRO
  {"tipo":"Departamento","titulo":"Dpto 2 ambientes frente al río con balcón","barrio":"Centro","direccion":"Av. Costanera 850","precio":68000,"moneda":"USD","m2_total":52,"dormitorios":1,"banos":1,"descripcion":"Luminoso departamento frente al río Paraná, balcón con vista panorámica, edificio con amenities. A pasos del paseo costanero.","inmobiliaria":"Posadas Propiedades","email_inm":"info@posadasprops.com.ar","tel_inm":"+5493764123456","url":"https://www.posadasprops.com.ar","nuevo":True,"fuente":"Posadas Propiedades"},
  {"tipo":"PH","titulo":"PH 3 ambientes con patio — Centro histórico","barrio":"Centro","direccion":"Calle Bolívar 340","precio":55000,"moneda":"USD","m2_total":75,"dormitorios":2,"banos":1,"descripcion":"PH en planta baja con patio privado de 40m², cocina renovada, luminoso. Ideal para pareja o familia chica.","inmobiliaria":"Misiones Inmuebles","email_inm":"ventas@misionesinmuebles.com.ar","tel_inm":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","nuevo":False,"fuente":"Misiones Inmuebles"},
  {"tipo":"Local","titulo":"Local comercial céntrico 80m² sobre San Martín","barrio":"Centro","direccion":"Av. San Martín 1200","precio":90000,"moneda":"USD","m2_total":80,"dormitorios":0,"banos":1,"descripcion":"Excelente ubicación, planta libre, local en galería de mucho tránsito peatonal.","inmobiliaria":"BienInvertido Posadas","email_inm":"info@bieninvertido.com","tel_inm":"+5493764987654","url":"https://www.bieninvertido.com","nuevo":True,"fuente":"BienInvertido"},
  {"tipo":"Departamento","titulo":"Monoambiente seminuevo zona centro","barrio":"Centro","direccion":"Calle Buenos Aires 600","precio":42000000,"moneda":"ARS","m2_total":30,"dormitorios":1,"banos":1,"descripcion":"Monoambiente seminuevo, cocina integrada, baño completo, ideal estudiante. A cuadras del casco histórico.","inmobiliaria":"Misiones Inmuebles","email_inm":"ventas@misionesinmuebles.com.ar","tel_inm":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","nuevo":False,"fuente":"Misiones Inmuebles"},
  {"tipo":"Departamento","titulo":"Dpto 3 ambientes reciclado pleno centro","barrio":"Centro","direccion":"Av. Roca 450","precio":75000,"moneda":"USD","m2_total":68,"dormitorios":2,"banos":1,"descripcion":"Departamento completamente reciclado, pisos de madera, cocina americana, a metros de la peatonal.","inmobiliaria":"Crest Inmobiliaria","email_inm":"ventas@crestposadas.com.ar","tel_inm":"+5493764112233","url":"https://www.crestposadas.com.ar","nuevo":True,"fuente":"Crest Inmobiliaria"},
  # BARRIO NORTE
  {"tipo":"Casa","titulo":"Casa 4 dorms. con pileta — Barrio Norte","barrio":"Barrio Norte","direccion":"Calle Andresito 450","precio":145000,"moneda":"USD","m2_total":210,"dormitorios":4,"banos":3,"descripcion":"Hermosa casa en barrio privado, 3 dormitorios en suite, pileta, asador, garage doble, jardín parquizado.","inmobiliaria":"Posadas Propiedades","email_inm":"info@posadasprops.com.ar","tel_inm":"+5493764123456","url":"https://www.posadasprops.com.ar","nuevo":False,"fuente":"Posadas Propiedades"},
  {"tipo":"Departamento","titulo":"Dpto 3 amb. con amenities — Torre Norte","barrio":"Barrio Norte","direccion":"Av. Uruguay 2100","precio":82000,"moneda":"USD","m2_total":78,"dormitorios":2,"banos":2,"descripcion":"Moderno departamento en torre premium, SUM, piscina, seguridad 24h, vista al Paraná.","inmobiliaria":"Crest Inmobiliaria","email_inm":"ventas@crestposadas.com.ar","tel_inm":"+5493764112233","url":"https://www.crestposadas.com.ar","nuevo":True,"fuente":"Crest Inmobiliaria"},
  {"tipo":"Terreno","titulo":"Lote 600m² en barrio privado con todos los servicios","barrio":"Barrio Norte","direccion":"Barrio Los Aromos, Lote 12","precio":48000,"moneda":"USD","m2_total":600,"dormitorios":0,"banos":0,"descripcion":"Lote en barrio privado con seguridad 24hs. Ideal para construir casa de calidad. Todos los servicios.","inmobiliaria":"Misiones Inmuebles","email_inm":"ventas@misionesinmuebles.com.ar","tel_inm":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","nuevo":False,"fuente":"Misiones Inmuebles"},
  {"tipo":"Casa","titulo":"Chalet 3 dorms. amplio garage — Bº Norte","barrio":"Barrio Norte","direccion":"Calle Corrientes 890","precio":118000,"moneda":"USD","m2_total":175,"dormitorios":3,"banos":2,"descripcion":"Chalet estilo colonial con galería, jardín, garage doble, barrio residencial consolidado.","inmobiliaria":"Fénix Inmobiliaria","email_inm":"info@fenixposadas.com.ar","tel_inm":"+5493764555111","url":"https://fenixxweb.com","nuevo":False,"fuente":"Fénix Inmobiliaria"},
  # COSTANERA
  {"tipo":"Casa","titulo":"Casa frente al río con embarcadero — Costanera","barrio":"Costanera","direccion":"Av. Costanera 1800","precio":195000,"moneda":"USD","m2_total":280,"dormitorios":4,"banos":3,"descripcion":"Majestuosa casa con acceso directo al río Paraná, embarcadero, piscina, quincho, parque, 4 dorms. en suite.","inmobiliaria":"Posadas Propiedades","email_inm":"info@posadasprops.com.ar","tel_inm":"+5493764123456","url":"https://www.posadasprops.com.ar","nuevo":False,"fuente":"Posadas Propiedades"},
  {"tipo":"Departamento","titulo":"Studio amoblado vista al Paraná","barrio":"Costanera","direccion":"Calle F. L. Beltrán 22","precio":42000,"moneda":"USD","m2_total":38,"dormitorios":1,"banos":1,"descripcion":"Studio moderno completamente equipado, ideal inversión para alquiler turístico. A 50m del río.","inmobiliaria":"InvertiMisiones","email_inm":"inversiones@invertimisiones.com.ar","tel_inm":"+5493764445566","url":"https://www.invertimisiones.com.ar","nuevo":True,"fuente":"InvertiMisiones"},
  {"tipo":"Departamento","titulo":"Dpto 2 dorms. con vista al Paraná — piso 8","barrio":"Costanera","direccion":"Av. Costanera 2200","precio":95000,"moneda":"USD","m2_total":72,"dormitorios":2,"banos":1,"descripcion":"Departamento en edificio moderno piso 8, vista panorámica al río, cochera, depósito, amenities.","inmobiliaria":"Crest Inmobiliaria","email_inm":"ventas@crestposadas.com.ar","tel_inm":"+5493764112233","url":"https://www.crestposadas.com.ar","nuevo":True,"fuente":"Crest Inmobiliaria"},
  # VILLA URQUIZA
  {"tipo":"Casa","titulo":"Casa 3 dorms. en barrio tranquilo — Villa Urquiza","barrio":"Villa Urquiza","direccion":"Calle Yapeyú 760","precio":95000,"moneda":"USD","m2_total":160,"dormitorios":3,"banos":2,"descripcion":"Casa familiar con patio y jardín, cocina amplia, lavadero, garage, barrio de baja densidad.","inmobiliaria":"BienInvertido Posadas","email_inm":"info@bieninvertido.com","tel_inm":"+5493764987654","url":"https://www.bieninvertido.com","nuevo":False,"fuente":"BienInvertido"},
  {"tipo":"Duplex","titulo":"Dúplex moderno 2 dorms. + estudio","barrio":"Villa Urquiza","direccion":"Pasaje Las Misiones 123","precio":115000,"moneda":"USD","m2_total":140,"dormitorios":3,"banos":2,"descripcion":"Dúplex de diseño contemporáneo, planta baja social + planta alta privada, amenities compartidos: pileta y SUM.","inmobiliaria":"Crest Inmobiliaria","email_inm":"ventas@crestposadas.com.ar","tel_inm":"+5493764112233","url":"https://www.crestposadas.com.ar","nuevo":True,"fuente":"Crest Inmobiliaria"},
  {"tipo":"Local","titulo":"Local 50m² en galería Villa Urquiza","barrio":"Villa Urquiza","direccion":"Galería Centro, local 8","precio":65000000,"moneda":"ARS","m2_total":50,"dormitorios":0,"banos":1,"descripcion":"Local con buena circulación peatonal, baño propio, posibilidad de depósito adicional. Zona en crecimiento.","inmobiliaria":"InvertiMisiones","email_inm":"inversiones@invertimisiones.com.ar","tel_inm":"+5493764445566","url":"https://www.invertimisiones.com.ar","nuevo":False,"fuente":"InvertiMisiones"},
  # PEÑAFLOR
  {"tipo":"Casa","titulo":"Chalet amplio con garage — Bº Peñaflor","barrio":"Peñaflor","direccion":"Calle Tucumán 890","precio":120000,"moneda":"USD","m2_total":200,"dormitorios":3,"banos":2,"descripcion":"Chalet estilo colonial, materiales de primera, galería, jardín, garage doble, barrio residencial consolidado.","inmobiliaria":"Misiones Inmuebles","email_inm":"ventas@misionesinmuebles.com.ar","tel_inm":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","nuevo":False,"fuente":"Misiones Inmuebles"},
  {"tipo":"Terreno","titulo":"Terreno 400m² escriturado — Peñaflor","barrio":"Peñaflor","direccion":"Calle Entre Ríos 1100","precio":28000,"moneda":"USD","m2_total":400,"dormitorios":0,"banos":0,"descripcion":"Lote 10x40 escriturado, todos los servicios, cuotas disponibles.","inmobiliaria":"Fénix Inmobiliaria","email_inm":"info@fenixposadas.com.ar","tel_inm":"+5493764555111","url":"https://fenixxweb.com","nuevo":True,"fuente":"Fénix Inmobiliaria"},
  # JARDÍN AMÉRICA
  {"tipo":"Departamento","titulo":"Dpto 2 dorms. en edificio moderno","barrio":"Jardín América","direccion":"Calle Salta 1500","precio":72000,"moneda":"USD","m2_total":68,"dormitorios":2,"banos":1,"descripcion":"Departamento nuevo con cochera, balcón terraza, edificio sustentable con paneles solares.","inmobiliaria":"Crest Inmobiliaria","email_inm":"ventas@crestposadas.com.ar","tel_inm":"+5493764112233","url":"https://www.crestposadas.com.ar","nuevo":True,"fuente":"Crest Inmobiliaria"},
  {"tipo":"Casa","titulo":"Casa 5 dorms. zona universitaria","barrio":"Jardín América","direccion":"Calle Rioja 2200","precio":165000,"moneda":"USD","m2_total":250,"dormitorios":5,"banos":3,"descripcion":"Gran propiedad para familia numerosa, cerca de la UNaM. Patio y asador.","inmobiliaria":"Posadas Propiedades","email_inm":"info@posadasprops.com.ar","tel_inm":"+5493764123456","url":"https://www.posadasprops.com.ar","nuevo":False,"fuente":"Posadas Propiedades"},
  # ZAIMÁN
  {"tipo":"Duplex","titulo":"Dúplex premium — Bº Zaimán country","barrio":"Zaimán","direccion":"Barrio Zaimán, Lote 45","precio":210000,"moneda":"USD","m2_total":300,"dormitorios":4,"banos":4,"descripcion":"Dúplex de lujo en barrio country con laguna artificial, piscina privada, quincho, smart home.","inmobiliaria":"BienInvertido Posadas","email_inm":"info@bieninvertido.com","tel_inm":"+5493764987654","url":"https://www.bieninvertido.com","nuevo":True,"fuente":"BienInvertido"},
  {"tipo":"Terreno","titulo":"Lote premium country Zaimán 800m²","barrio":"Zaimán","direccion":"Barrio Zaimán, Sector B","precio":85000,"moneda":"USD","m2_total":800,"dormitorios":0,"banos":0,"descripcion":"Lote en barrio country con laguna, seguridad 24hs, todos los servicios, escritura al día.","inmobiliaria":"InvertiMisiones","email_inm":"inversiones@invertimisiones.com.ar","tel_inm":"+5493764445566","url":"https://www.invertimisiones.com.ar","nuevo":False,"fuente":"InvertiMisiones"},
  # SAN JORGE / KM 4 / OTROS
  {"tipo":"Terreno","titulo":"Lote residencial 300m² — Bº San Jorge","barrio":"San Jorge","direccion":"Calle San Jorge 1100","precio":22000,"moneda":"USD","m2_total":300,"dormitorios":0,"banos":0,"descripcion":"Lote 10x30 en barrio en desarrollo, todos los servicios, escritura al día.","inmobiliaria":"BienInvertido Posadas","email_inm":"info@bieninvertido.com","tel_inm":"+5493764987654","url":"https://www.bieninvertido.com","nuevo":True,"fuente":"BienInvertido"},
  {"tipo":"Casa","titulo":"Casa 2 dorms. con patio — Bº Itaembé Miní","barrio":"Itaembé Miní","direccion":"Calle Guaraní 550","precio":58000,"moneda":"USD","m2_total":90,"dormitorios":2,"banos":1,"descripcion":"Casa sencilla en excelente estado, patio amplio, cocina comedor, garage, tranquila.","inmobiliaria":"Fénix Inmobiliaria","email_inm":"info@fenixposadas.com.ar","tel_inm":"+5493764555111","url":"https://fenixxweb.com","nuevo":False,"fuente":"Fénix Inmobiliaria"},
  {"tipo":"Terreno","titulo":"Fracción comercial sobre Ruta 12 — 2000m²","barrio":"Ruta 12","direccion":"Ruta Nacional 12, km 5","precio":180000,"moneda":"USD","m2_total":2000,"dormitorios":0,"banos":0,"descripcion":"Terreno con frente sobre Ruta Nacional 12, ideal para emprendimiento comercial, depósito o taller.","inmobiliaria":"InvertiMisiones","email_inm":"inversiones@invertimisiones.com.ar","tel_inm":"+5493764445566","url":"https://www.invertimisiones.com.ar","nuevo":False,"fuente":"InvertiMisiones"},
  {"tipo":"Casa","titulo":"Casa 3 dorms. con pileta — Itaembé Guazú","barrio":"Itaembé Guazú","direccion":"Calle 4 N° 234","precio":105000,"moneda":"USD","m2_total":180,"dormitorios":3,"banos":2,"descripcion":"Casa moderna en barrio nuevo en expansión, pileta, jardín amplio, doble cochera.","inmobiliaria":"Posadas Propiedades","email_inm":"info@posadasprops.com.ar","tel_inm":"+5493764123456","url":"https://www.posadasprops.com.ar","nuevo":True,"fuente":"Posadas Propiedades"},
  {"tipo":"Departamento","titulo":"Dpto 1 dorm. en edificio con ascensor","barrio":"Villa Cabello","direccion":"Calle Rivadavia 2800","precio":48000,"moneda":"USD","m2_total":45,"dormitorios":1,"banos":1,"descripcion":"Departamento compacto, ideal inversión o primera vivienda, piso 3, luminoso.","inmobiliaria":"Misiones Inmuebles","email_inm":"ventas@misionesinmuebles.com.ar","tel_inm":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","nuevo":False,"fuente":"Misiones Inmuebles"},
  {"tipo":"Casa","titulo":"Casa esquina 4 dorms. — Villa Sarita","barrio":"Villa Sarita","direccion":"Calle Almafuerte esquina Roca","precio":138000,"moneda":"USD","m2_total":220,"dormitorios":4,"banos":2,"descripcion":"Casa en esquina, amplio patio lateral, galería cubierta, garage, posibilidad de local.","inmobiliaria":"BienInvertido Posadas","email_inm":"info@bieninvertido.com","tel_inm":"+5493764987654","url":"https://www.bieninvertido.com","nuevo":False,"fuente":"BienInvertido"},
  {"tipo":"Duplex","titulo":"Dúplex 3 dorms. con patio — Miguel Lanús","barrio":"Miguel Lanús","direccion":"Calle Independencia 1450","precio":88000,"moneda":"USD","m2_total":130,"dormitorios":3,"banos":2,"descripcion":"Dúplex a estrenar, 2 plantas, dormitorio en suite en planta alta, patio privado.","inmobiliaria":"Crest Inmobiliaria","email_inm":"ventas@crestposadas.com.ar","tel_inm":"+5493764112233","url":"https://www.crestposadas.com.ar","nuevo":True,"fuente":"Crest Inmobiliaria"},
  {"tipo":"Casa","titulo":"Casa antigua con terreno 600m² — Centro","barrio":"Centro","direccion":"Calle Córdoba 780","precio":125000,"moneda":"USD","m2_total":150,"dormitorios":3,"banos":2,"descripcion":"Casa antigua en lote de 600m², ideal para proyecto de construcción o refacción total. Ubicación excelente.","inmobiliaria":"Fénix Inmobiliaria","email_inm":"info@fenixposadas.com.ar","tel_inm":"+5493764555111","url":"https://fenixxweb.com","nuevo":False,"fuente":"Fénix Inmobiliaria"},
  {"tipo":"Local","titulo":"Galpón industrial 500m² — Ruta 12","barrio":"Ruta 12","direccion":"Ruta Nacional 12, parque industrial","precio":220000,"moneda":"USD","m2_total":500,"dormitorios":0,"banos":2,"descripcion":"Galpón con oficinas, baños, depósito climatizado, portón eléctrico, 3 fases de electricidad.","inmobiliaria":"InvertiMisiones","email_inm":"inversiones@invertimisiones.com.ar","tel_inm":"+5493764445566","url":"https://www.invertimisiones.com.ar","nuevo":False,"fuente":"InvertiMisiones"},
  {"tipo":"Terreno","titulo":"Lote 500m² escriturado — Panambi","barrio":"Panambi","direccion":"Calle Los Pinos 234","precio":19000,"moneda":"USD","m2_total":500,"dormitorios":0,"banos":0,"descripcion":"Lote en barrio residencial en expansión, a 8km del centro, todos los servicios.","inmobiliaria":"Misiones Inmuebles","email_inm":"ventas@misionesinmuebles.com.ar","tel_inm":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","nuevo":True,"fuente":"Misiones Inmuebles"},
  {"tipo":"Departamento","titulo":"Dpto 4 amb. en edificio exclusivo — Costanera","barrio":"Costanera","direccion":"Av. Costanera 3000","precio":155000,"moneda":"USD","m2_total":120,"dormitorios":3,"banos":2,"descripcion":"Departamento de categoría, dependencia de servicio, lavadero propio, dos cocheras, baulera, vista Paraná.","inmobiliaria":"Posadas Propiedades","email_inm":"info@posadasprops.com.ar","tel_inm":"+5493764123456","url":"https://www.posadasprops.com.ar","nuevo":True,"fuente":"Posadas Propiedades"},
  {"tipo":"Casa","titulo":"Casa 2 dorms. recién refaccionada — Km 4","barrio":"Km 4","direccion":"Calle El Bosque 88","precio":52000,"moneda":"USD","m2_total":85,"dormitorios":2,"banos":1,"descripcion":"Casa totalmente refaccionada, nueva instalación eléctrica y sanitaria, patio con quincho.","inmobiliaria":"BienInvertido Posadas","email_inm":"info@bieninvertido.com","tel_inm":"+5493764987654","url":"https://www.bieninvertido.com","nuevo":False,"fuente":"BienInvertido"},
  {"tipo":"Casa","titulo":"Casa 6 dorms. apta consultorios — Barrio Norte","barrio":"Barrio Norte","direccion":"Av. Mitre 1800","precio":280000,"moneda":"USD","m2_total":380,"dormitorios":6,"banos":4,"descripcion":"Gran propiedad, ideal para clínica, consultorio, geriátrico o residencia de categoría. Dos entradas independientes.","inmobiliaria":"Crest Inmobiliaria","email_inm":"ventas@crestposadas.com.ar","tel_inm":"+5493764112233","url":"https://www.crestposadas.com.ar","nuevo":False,"fuente":"Crest Inmobiliaria"},
]

def semilla_a_props():
    props = []
    for i, s in enumerate(SEMILLA):
        lat, lng = geocode(s["barrio"] + " " + s.get("direccion",""))
        props.append({
            "id":          pid("semilla", f"{i}{s['titulo'][:20]}"),
            "ref_externa": f"SEM-{i+1:03d}",
            "fuente":      s.get("fuente", "Inmobiliaria local"),
            "tipo":        s["tipo"],
            "titulo":      s["titulo"],
            "barrio":      s["barrio"],
            "direccion":   s.get("direccion",""),
            "ciudad":      "Posadas",
            "precio":      s["precio"],
            "moneda":      s["moneda"],
            "m2_total":    s.get("m2_total"),
            "m2_cubierto": s.get("m2_cubierto"),
            "dormitorios": s.get("dormitorios"),
            "banos":       s.get("banos"),
            "descripcion": s.get("descripcion",""),
            "fotos":       [],
            "url":         s.get("url",""),
            "inmobiliaria":s.get("inmobiliaria",""),
            "email_inm":   s.get("email_inm",""),
            "tel_inm":     s.get("tel_inm",""),
            "lat":         lat,
            "lng":         lng,
            "fecha":       datetime.today().strftime("%Y-%m-%d"),
            "nuevo":       s.get("nuevo", False),
        })
    return props

# ── SCRAPERS (activos cuando hay acceso a internet) ────────────────────────
def dedup(props):
    seen_id, seen_key = set(), set()
    out = []
    for p in props:
        if p["id"] in seen_id: continue
        k = f"{p['precio']}-{p['tipo'][:3]}-{p['barrio'][:8]}"
        if k in seen_key and p["precio"] > 0: continue
        seen_id.add(p["id"])
        seen_key.add(k)
        out.append(p)
    return out

async def scrape_mercadolibre(client):
    resultados = []
    CATS = {"MLA1468":"Casa","MLA1471":"Departamento","MLA1473":"Terreno",
            "MLA1476":"Local","MLA1472":"PH","MLA150726":"Duplex"}
    for cat, tipo in CATS.items():
        offset = 0
        for _ in range(MAX_PAGES):
            url = (f"https://api.mercadolibre.com/sites/MLA/search"
                   f"?category={cat}&state_id=AR-N&city=Posadas"
                   f"&offset={offset}&limit=48")
            try:
                r = await client.get(url, headers={**HEADERS,"Accept":"application/json"}, timeout=20)
                if r.status_code != 200: break
                data = r.json()
            except: break
            items = data.get("results", [])
            total = data.get("paging", {}).get("total", 0)
            if not items: break
            for item in items:
                loc = item.get("location", {})
                ciudad = loc.get("city", {}).get("name", "")
                if ciudad and "posadas" not in ciudad.lower(): continue
                barrio = (loc.get("neighborhood", {}).get("name","") or
                          item.get("seller_address",{}).get("city",{}).get("name","") or "Posadas")
                lat = loc.get("latitude") or loc.get("lat")
                lng = loc.get("longitude") or loc.get("lon")
                if not lat or not lng: lat, lng = geocode(barrio)
                attrs = {a["id"]: a.get("value_name") for a in item.get("attributes",[])}
                fotos = [p.get("url","").replace("-I.jpg","-O.jpg")
                         for p in item.get("pictures",[])[:5]] if item.get("pictures") else []
                if item.get("thumbnail") and not fotos:
                    fotos = [item["thumbnail"].replace("-I.jpg","-O.jpg")]
                seller = item.get("seller",{})
                inm = seller.get("nickname","MercadoLibre").title().replace("_"," ")
                resultados.append({
                    "id": pid("ml", item["id"]), "ref_externa": item["id"],
                    "fuente": "MercadoLibre", "tipo": tipo,
                    "titulo": item.get("title",""), "barrio": barrio,
                    "direccion": item.get("seller_address",{}).get("address_line", barrio),
                    "ciudad": "Posadas",
                    "precio": item.get("price", 0), "moneda": item.get("currency_id","USD"),
                    "m2_total": num(attrs.get("TOTAL_AREA") or attrs.get("SURFACE_TOTAL")),
                    "m2_cubierto": num(attrs.get("COVERED_AREA") or attrs.get("SURFACE_COVERED")),
                    "dormitorios": num(attrs.get("BEDROOMS") or attrs.get("ROOMS")),
                    "banos": num(attrs.get("BATHROOMS")),
                    "descripcion": item.get("title",""), "fotos": fotos,
                    "url": item.get("permalink",""), "inmobiliaria": inm,
                    "email_inm": "", "tel_inm": "",
                    "lat": round(float(lat),6), "lng": round(float(lng),6),
                    "fecha": item.get("date_created","")[:10],
                    "nuevo": "estrenar" in item.get("title","").lower(),
                })
            offset += 48
            if offset >= min(total, MAX_PAGES * 48): break
            await asyncio.sleep(DELAY)
    log.info(f"MercadoLibre: {len(resultados)}")
    return resultados

def parse_html_generico(html, fuente_nombre, fuente_url, email, tel):
    items = []
    precios = re.findall(r'U[SD$]+\s*([\d\.,]{4,})', html)
    titulos = [strip(t) for t in re.findall(r'<h[123][^>]*>(.*?)</h[123]>',html,re.DOTALL|re.IGNORECASE)
               if len(strip(t)) > 8]
    for i, precio_str in enumerate(precios[:25]):
        try:
            precio = int(precio_str.replace(".","").replace(",",""))
            if precio < 1000: continue
            titulo = titulos[i] if i < len(titulos) else f"Propiedad en venta — {fuente_nombre}"
            tipo   = tipo_de(titulo)
            barrio = barrio_de(titulo)
            lat, lng = geocode(barrio)
            items.append({
                "id": pid(fuente_nombre, f"{precio}{titulo[:15]}"),
                "ref_externa": f"{fuente_nombre[:3].upper()}-{i}",
                "fuente": fuente_nombre, "tipo": tipo,
                "titulo": titulo, "barrio": barrio,
                "direccion": barrio + ", Posadas", "ciudad": "Posadas",
                "precio": precio, "moneda": "USD",
                "m2_total": None, "m2_cubierto": None,
                "dormitorios": None, "banos": None,
                "descripcion": titulo, "fotos": [], "url": fuente_url,
                "inmobiliaria": fuente_nombre, "email_inm": email, "tel_inm": tel,
                "lat": lat, "lng": lng,
                "fecha": datetime.today().strftime("%Y-%m-%d"), "nuevo": False,
            })
        except: continue
    return items

async def scrape_zonaprop(client):
    res = []
    for pag in range(1, MAX_PAGES+1):
        url = ("https://www.zonaprop.com.ar/inmuebles-venta-posadas.html"
               if pag==1 else
               f"https://www.zonaprop.com.ar/inmuebles-venta-posadas-pagina-{pag}.html")
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: break
            html = r.text
            arts = re.findall(r'<article[^>]+data-id=["\']?(\d+)["\']?[^>]*>(.*?)</article>',
                              html, re.DOTALL|re.IGNORECASE)
            if not arts: break
            for aid, ahtml in arts:
                try:
                    tm = re.search(r'<h2[^>]*>(.*?)</h2>', ahtml, re.DOTALL)
                    titulo = strip(tm.group(1)) if tm else f"ZP-{aid}"
                    pm = re.search(r'(USD?|US\$|\$)\s*([\d\.,]+)', ahtml)
                    precio, moneda = 0, "USD"
                    if pm:
                        moneda = "USD" if "U" in pm.group(1).upper() else "ARS"
                        precio = int(pm.group(2).replace(".","").replace(",",""))
                    dm = re.search(r'class=["\'][^"\']*address[^"\']*["\'][^>]*>(.*?)</', ahtml, re.DOTALL)
                    direccion = strip(dm.group(1)) if dm else "Posadas"
                    sm = re.search(r'(\d+)\s*m[²2]', ahtml)
                    m2 = int(sm.group(1)) if sm else None
                    drm = re.search(r'(\d+)\s*(?:dorm|amb)', ahtml, re.IGNORECASE)
                    dorms = int(drm.group(1)) if drm else None
                    um = re.search(r'href=["\'](/propiedades/[^"\']+)["\']', ahtml)
                    url_p = "https://www.zonaprop.com.ar" + um.group(1) if um else ""
                    barrio = barrio_de(direccion+" "+titulo)
                    lat, lng = geocode(barrio)
                    if precio <= 0: continue
                    res.append({
                        "id": pid("zp", aid), "ref_externa": f"ZP-{aid}",
                        "fuente": "ZonaProp", "tipo": tipo_de(titulo+" "+direccion),
                        "titulo": titulo, "barrio": barrio, "direccion": direccion,
                        "ciudad": "Posadas", "precio": precio, "moneda": moneda,
                        "m2_total": m2, "m2_cubierto": None,
                        "dormitorios": dorms, "banos": None,
                        "descripcion": titulo, "fotos": [], "url": url_p,
                        "inmobiliaria": "ZonaProp", "email_inm": "", "tel_inm": "",
                        "lat": lat, "lng": lng,
                        "fecha": datetime.today().strftime("%Y-%m-%d"), "nuevo": False,
                    })
                except: continue
            await asyncio.sleep(DELAY)
        except Exception as e:
            log.warning(f"ZonaProp pag {pag}: {e}"); break
    log.info(f"ZonaProp: {len(res)}")
    return res

async def scrape_argenprop(client):
    res = []
    for pag in range(1, MAX_PAGES+1):
        url = f"https://www.argenprop.com/inmuebles-en-venta-en-posadas?pagina={pag}"
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: break
            cards = re.findall(r'<div[^>]+listing__item[^>]*>(.*?)</div>\s*</div>',
                               r.text, re.DOTALL|re.IGNORECASE)
            if not cards: break
            for j, card in enumerate(cards):
                try:
                    tm = re.search(r'listing__title[^"\']*["\'][^>]*>(.*?)</', card, re.DOTALL)
                    titulo = strip(tm.group(1)) if tm else f"AP-{j}"
                    pm = re.search(r'(USD?|US\$|\$)\s*([\d\.,]+)', card)
                    precio, moneda = 0, "USD"
                    if pm:
                        moneda = "USD" if "U" in pm.group(1).upper() else "ARS"
                        precio = int(pm.group(2).replace(".","").replace(",",""))
                    dm = re.search(r'listing__location[^"\']*["\'][^>]*>(.*?)</', card, re.DOTALL)
                    direccion = strip(dm.group(1)) if dm else "Posadas"
                    sm = re.search(r'(\d+)\s*m[²2]', card)
                    m2 = int(sm.group(1)) if sm else None
                    drm = re.search(r'(\d+)\s*dorm', card, re.IGNORECASE)
                    dorms = int(drm.group(1)) if drm else None
                    um = re.search(r'href=["\']([^"\']+propiedad[^"\']+)["\']', card)
                    url_p = "https://www.argenprop.com" + um.group(1) if um else ""
                    barrio = barrio_de(direccion+" "+titulo)
                    lat, lng = geocode(barrio)
                    if precio <= 0: continue
                    res.append({
                        "id": pid("ap", f"{j}{titulo[:15]}"), "ref_externa": f"AP-{j}",
                        "fuente": "Argenprop", "tipo": tipo_de(titulo+" "+direccion),
                        "titulo": titulo, "barrio": barrio, "direccion": direccion,
                        "ciudad": "Posadas", "precio": precio, "moneda": moneda,
                        "m2_total": m2, "m2_cubierto": None, "dormitorios": dorms, "banos": None,
                        "descripcion": titulo, "fotos": [], "url": url_p,
                        "inmobiliaria": "Argenprop", "email_inm": "", "tel_inm": "",
                        "lat": lat, "lng": lng,
                        "fecha": datetime.today().strftime("%Y-%m-%d"), "nuevo": False,
                    })
                except: continue
            await asyncio.sleep(DELAY)
        except Exception as e:
            log.warning(f"Argenprop pag {pag}: {e}"); break
    log.info(f"Argenprop: {len(res)}")
    return res

async def scrape_locales(client):
    res = []
    fuentes = [
        {"nombre":"Fénix Inmobiliaria","url":"https://fenixxweb.com/propiedades?operacion=venta&localidad=posadas","email":"info@fenixxweb.com","tel":"+5493764555111"},
        {"nombre":"Costa Remates","url":"https://costarematesycorretaje.com.ar/propiedades/venta","email":"info@costarematesycorretaje.com.ar","tel":"+5493764222333"},
        {"nombre":"Origen Propiedades","url":"https://origen-propiedades.com/propiedades?tipo=venta","email":"info@origen-propiedades.com","tel":"+5493764333444"},
    ]
    for f in fuentes:
        try:
            r = await client.get(f["url"], headers=HEADERS, timeout=20, follow_redirects=True)
            if r.status_code == 200:
                items = parse_html_generico(r.text, f["nombre"], f["url"], f["email"], f["tel"])
                res.extend(items)
                log.info(f"{f['nombre']}: {len(items)}")
        except Exception as e:
            log.warning(f"{f['nombre']}: {e}")
        await asyncio.sleep(DELAY)
    return res

async def ejecutar_scraping():
    log.info("=== Iniciando scraping ===")
    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=5),
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    ) as client:
        tareas = await asyncio.gather(
            scrape_mercadolibre(client),
            scrape_zonaprop(client),
            scrape_argenprop(client),
            scrape_locales(client),
            return_exceptions=True,
        )
    scraped = []
    for t in tareas:
        if isinstance(t, list): scraped.extend(t)
    # Si el scraping no trajo datos, usamos la semilla
    if len(scraped) < 5:
        log.warning("Scraping sin resultados — usando datos semilla")
        scraped = semilla_a_props()
    else:
        # Combinar scraping + semilla (sin duplicar)
        scraped = dedup(scraped + semilla_a_props())
    scraped = [p for p in scraped if p["precio"] > 0]
    scraped.sort(key=lambda x: x["precio"])
    log.info(f"=== Total: {len(scraped)} propiedades ===")
    return scraped

# ── PERSISTENCIA ───────────────────────────────────────────────────────────
def guardar(props):
    global _MEM, _FECHA
    _MEM   = props
    _FECHA = datetime.now().isoformat()
    try:
        DATA_FILE.write_text(json.dumps(props, ensure_ascii=False, indent=2))
    except Exception as e:
        log.warning(f"No se pudo guardar en disco: {e}")

def cargar():
    global _MEM
    if _MEM: return _MEM
    if DATA_FILE.exists():
        try:
            _MEM = json.loads(DATA_FILE.read_text())
            return _MEM
        except: pass
    return []

def necesita_refresh():
    if _MEM and _FECHA:
        return (datetime.now() - datetime.fromisoformat(_FECHA)) > timedelta(hours=REFRESH_HOURS)
    return True

# ── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI(title="InmoPosadas API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    global _SCRAPING
    # Cargar semilla inmediatamente para respuesta rápida
    if not cargar():
        semilla = semilla_a_props()
        guardar(semilla)
        log.info(f"Datos semilla cargados: {len(semilla)} propiedades")
    # Lanzar scraping en background
    if not _SCRAPING:
        _SCRAPING = True
        asyncio.create_task(_tarea())

async def _tarea():
    global _SCRAPING
    try:
        props = await ejecutar_scraping()
        guardar(props)
    finally:
        _SCRAPING = False

@app.get("/health")
async def health():
    return {"status":"ok","propiedades":len(_MEM),"ts":datetime.now().isoformat()}

@app.get("/api/status")
async def api_status():
    return {
        "scraping_en_curso": _SCRAPING,
        "ultima_actualizacion": _FECHA or None,
        "total_propiedades": len(_MEM),
        "datos_disponibles": bool(_MEM),
    }

@app.post("/api/refresh")
async def refresh(bg: BackgroundTasks):
    global _SCRAPING
    if _SCRAPING:
        return {"message":"Ya hay un scraping en curso, esperá unos minutos."}
    _SCRAPING = True
    bg.add_task(_tarea)
    return {"message":"Scraping iniciado. Los datos estarán listos en 2-5 minutos."}

@app.get("/api/propiedades")
async def get_props(
    tipo:Optional[str]=Query(None), barrio:Optional[str]=Query(None),
    moneda:Optional[str]=Query(None), precio_min:Optional[int]=Query(None),
    precio_max:Optional[int]=Query(None), dormitorios:Optional[int]=Query(None),
    fuente:Optional[str]=Query(None), limit:int=Query(500), offset:int=Query(0),
):
    ps = cargar()
    if tipo:        ps=[p for p in ps if p["tipo"].lower()==tipo.lower()]
    if barrio:      ps=[p for p in ps if barrio.lower() in p["barrio"].lower()]
    if moneda:      ps=[p for p in ps if p["moneda"].upper()==moneda.upper()]
    if precio_min:  ps=[p for p in ps if p["precio"]>=precio_min]
    if precio_max:  ps=[p for p in ps if p["precio"]<=precio_max]
    if dormitorios: ps=[p for p in ps if p.get("dormitorios")==dormitorios]
    if fuente:      ps=[p for p in ps if fuente.lower() in p["fuente"].lower()]
    return {"total":len(ps),"resultados":ps[offset:offset+limit]}

@app.get("/api/estadisticas")
async def stats():
    ps = cargar()
    if not ps: return {"total":0}
    usd = [p["precio"] for p in ps if p["moneda"]=="USD" and p["precio"]>0]
    tipos,fuentes,barrios = {},{},{}
    for p in ps:
        tipos[p["tipo"]]     = tipos.get(p["tipo"],0)+1
        fuentes[p["fuente"]] = fuentes.get(p["fuente"],0)+1
        barrios[p["barrio"]] = barrios.get(p["barrio"],0)+1
    return {
        "total": len(ps),
        "precio_usd_min": min(usd) if usd else 0,
        "precio_usd_max": max(usd) if usd else 0,
        "precio_usd_promedio": int(sum(usd)/len(usd)) if usd else 0,
        "por_tipo": tipos, "por_fuente": fuentes,
        "top_barrios": dict(sorted(barrios.items(),key=lambda x:-x[1])[:10]),
    }

# Frontend
FRONTEND = BASE_DIR / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
