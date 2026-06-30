"""
InmoPosadas — Backend FastAPI v3
Scrapers con fotos reales, coordenadas precisas vía Nominatim y más propiedades.
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
MAX_PAGES     = 12          # páginas de listado por fuente
DELAY         = 1.0         # segundos entre requests
GEO_DELAY     = 1.2         # Nominatim pide ≤1 req/seg

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("inmopo")

_MEM:   list[dict] = []
_FECHA: str        = ""
_SCRAPING          = False

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

# ── COORDENADAS DE BARRIOS (fallback) ──────────────────────────────────────
BARRIOS = {
    "centro":            (-27.3671, -55.8964),
    "barrio norte":      (-27.3580, -55.9000),
    "costanera":         (-27.3720, -55.8950),
    "villa urquiza":     (-27.3700, -55.8800),
    "villa sarita":      (-27.3540, -55.8910),
    "villa cabello":     (-27.3550, -55.8850),
    "peñaflor":          (-27.3650, -55.9200),
    "jardín américa":    (-27.3740, -55.9100),
    "jardin america":    (-27.3740, -55.9100),
    "zaimán":            (-27.3820, -55.9150),
    "zaiman":            (-27.3820, -55.9150),
    "san jorge":         (-27.3830, -55.9200),
    "itaembé guazú":     (-27.3900, -55.8800),
    "itaembe guazu":     (-27.3900, -55.8800),
    "itaembé miní":      (-27.3550, -55.9100),
    "itaembe mini":      (-27.3550, -55.9100),
    "miguel lanús":      (-27.3680, -55.9050),
    "miguel lanus":      (-27.3680, -55.9050),
    "villa blosset":     (-27.3600, -55.9150),
    "km 4":              (-27.3850, -55.9050),
    "km 8":              (-27.3950, -55.9200),
    "ruta 12":           (-27.3620, -55.9300),
    "panambi":           (-27.3480, -55.9200),
    "kennedy":           (-27.3780, -55.9050),
    "el palmar":         (-27.3700, -55.9300),
    "santa catalina":    (-27.3750, -55.9100),
    "nueva esperanza":   (-27.3900, -55.9300),
    "el yerbal":         (-27.3900, -55.8800),
    "la eugenia":        (-27.4000, -55.9000),
    "palomar":           (-27.3760, -55.9000),
    "aguacate":          (-27.3900, -55.8950),
    "garupá":            (-27.4100, -55.8900),
    "garupa":            (-27.4100, -55.8900),
    "posadas":           (-27.3671, -55.8964),
}

# Cache de geocodificación para no repetir llamadas
_GEO_CACHE: dict = {}

def geocode_fallback(texto: str) -> tuple:
    t = texto.lower()
    for b, c in sorted(BARRIOS.items(), key=lambda x: -len(x[0])):
        if b in t:
            return (round(c[0] + random.uniform(-0.0025, 0.0025), 6),
                    round(c[1] + random.uniform(-0.0025, 0.0025), 6))
    return (round(-27.3671 + random.uniform(-0.02, 0.02), 6),
            round(-55.8964 + random.uniform(-0.02, 0.02), 6))

async def geocode_nominatim(client: httpx.AsyncClient, direccion: str) -> tuple | None:
    """Geocodifica una dirección con Nominatim (OpenStreetMap). Gratis, sin API key."""
    key = direccion.lower().strip()
    if key in _GEO_CACHE:
        return _GEO_CACHE[key]
    try:
        query = f"{direccion}, Posadas, Misiones, Argentina"
        r = await client.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "ar"},
            headers={**HEADERS, "User-Agent": "InmoPosadas/1.0 (inmopo@posadas.ar)"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                lat = round(float(data[0]["lat"]), 6)
                lng = round(float(data[0]["lon"]), 6)
                # Validar que está dentro de Posadas (bbox ~27.30–27.45 lat, ~55.80–55.97 lng)
                if -27.50 < lat < -27.20 and -56.10 < lng < -55.70:
                    _GEO_CACHE[key] = (lat, lng)
                    await asyncio.sleep(GEO_DELAY)  # respetar límite Nominatim
                    return (lat, lng)
    except Exception:
        pass
    return None

# ── UTILIDADES ─────────────────────────────────────────────────────────────
def pid(fuente, ref): return hashlib.md5(f"{fuente}:{ref}".encode()).hexdigest()[:12]
def num(v):
    try: return int(float(str(v or "").replace(",",".")))
    except: return None
def strip(s): return re.sub(r'<[^>]+>', '', str(s)).strip()

def tipo_de(t):
    t = t.lower()
    if any(x in t for x in ["duplex","dúplex"]): return "Duplex"
    if re.search(r'\bph\b|penthouse', t): return "PH"
    if any(x in t for x in ["departamento","depto","dpto","apartamento"]): return "Departamento"
    if any(x in t for x in ["terreno","lote","fracción","fraccion","campo","chacra"]): return "Terreno"
    if any(x in t for x in ["local","oficina","galpon","galpón","depósito","deposito"]): return "Local"
    return "Casa"

def barrio_de(t):
    t2 = t.lower()
    for b in sorted(BARRIOS.keys(), key=len, reverse=True):
        if b in t2 and b != "posadas": return b.title()
    return "Posadas"

def og_image(html: str) -> list[str]:
    """Extrae meta og:image de una ficha."""
    imgs = re.findall(r'meta-og:image:\s*(https?://[^\s\n]+)', html)
    if not imgs:
        imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    return [i for i in imgs if not re.search(r'logo|icon|favicon', i, re.IGNORECASE)][:1]

def dedup(props):
    seen_id, seen_key = set(), set()
    out = []
    for p in props:
        if p["id"] in seen_id: continue
        k = f"{p['precio']}-{p['tipo'][:3]}-{p['barrio'][:8]}"
        if k in seen_key and p["precio"] > 0: continue
        seen_id.add(p["id"]); seen_key.add(k)
        out.append(p)
    return out

def mk_prop(**kw) -> dict:
    return {
        "id": kw.get("id",""), "ref_externa": kw.get("ref",""),
        "fuente": kw.get("fuente",""), "tipo": kw.get("tipo","Casa"),
        "titulo": kw.get("titulo","")[:120],
        "barrio": kw.get("barrio","Posadas"),
        "direccion": kw.get("direccion","")[:120],
        "ciudad": "Posadas",
        "precio": kw.get("precio",0), "moneda": kw.get("moneda","USD"),
        "m2_total": kw.get("m2"), "m2_cubierto": kw.get("m2c"),
        "dormitorios": kw.get("dorms"), "banos": kw.get("banos"),
        "descripcion": kw.get("desc","")[:400],
        "fotos": kw.get("fotos",[]),
        "url": kw.get("url",""),
        "inmobiliaria": kw.get("inm",""),
        "email_inm": kw.get("email",""), "tel_inm": kw.get("tel",""),
        "lat": kw.get("lat",0.0), "lng": kw.get("lng",0.0),
        "fecha": kw.get("fecha", datetime.today().strftime("%Y-%m-%d")),
        "nuevo": kw.get("nuevo", False),
    }

# ── GEOCODIFICACIÓN CON GOOGLE MAPS EMBED (Torres) ──────────────────────────
def parse_gmaps_coords(texto: str) -> tuple | None:
    """Extrae coordenadas de URL de Google Maps: ?q=LAT,LNG"""
    m = re.search(r'maps\.google\.com[^"\']*[?&]q=([\-\d\.]+),([\-\d\.]+)', texto)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None

# ── MERCADOLIBRE ───────────────────────────────────────────────────────────
async def scrape_mercadolibre(client):
    res = []
    CATS = {
        "MLA1468":"Casa","MLA1471":"Departamento","MLA1473":"Terreno",
        "MLA1476":"Local","MLA1472":"PH","MLA150726":"Duplex",
    }
    for cat, tipo in CATS.items():
        offset = 0
        for _ in range(MAX_PAGES):
            url = (f"https://api.mercadolibre.com/sites/MLA/search"
                   f"?category={cat}&state_id=AR-N&city=Posadas&offset={offset}&limit=48")
            try:
                r = await client.get(url, headers={**HEADERS,"Accept":"application/json"}, timeout=20)
                if r.status_code != 200: break
                data = r.json()
            except: break
            items = data.get("results",[])
            total = data.get("paging",{}).get("total",0)
            if not items: break
            for item in items:
                loc = item.get("location",{})
                ciudad = loc.get("city",{}).get("name","")
                if ciudad and "posadas" not in ciudad.lower(): continue
                barrio = (loc.get("neighborhood",{}).get("name","") or
                          item.get("seller_address",{}).get("city",{}).get("name","") or "Posadas")
                lat = loc.get("latitude") or loc.get("lat")
                lng = loc.get("longitude") or loc.get("lon")
                if not lat or not lng: lat,lng = geocode_fallback(barrio)
                attrs = {a["id"]: a.get("value_name") for a in item.get("attributes",[])}
                fotos = [p.get("url","").replace("-I.jpg","-O.jpg")
                         for p in item.get("pictures",[])[:8] if p.get("url")]
                if item.get("thumbnail") and not fotos:
                    fotos = [item["thumbnail"].replace("-I.jpg","-O.jpg")]
                seller = item.get("seller",{})
                res.append(mk_prop(
                    id=pid("ml",item["id"]), ref=item["id"], fuente="MercadoLibre",
                    tipo=tipo, titulo=item.get("title",""), barrio=barrio,
                    direccion=item.get("seller_address",{}).get("address_line",barrio),
                    precio=item.get("price",0), moneda=item.get("currency_id","USD"),
                    m2=num(attrs.get("TOTAL_AREA") or attrs.get("SURFACE_TOTAL")),
                    m2c=num(attrs.get("COVERED_AREA") or attrs.get("SURFACE_COVERED")),
                    dorms=num(attrs.get("BEDROOMS") or attrs.get("ROOMS")),
                    banos=num(attrs.get("BATHROOMS")),
                    desc=item.get("title",""), fotos=fotos,
                    url=item.get("permalink",""),
                    inm=seller.get("nickname","MercadoLibre").title().replace("_"," "),
                    lat=round(float(lat),6), lng=round(float(lng),6),
                    fecha=item.get("date_created","")[:10],
                    nuevo="estrenar" in item.get("title","").lower(),
                ))
            offset += 48
            if offset >= min(total, MAX_PAGES*48): break
            await asyncio.sleep(DELAY)
    log.info(f"MercadoLibre: {len(res)}")
    return res

# ── ZONAPROP ───────────────────────────────────────────────────────────────
async def scrape_zonaprop(client):
    res = []
    for pag in range(1, MAX_PAGES+1):
        url = ("https://www.zonaprop.com.ar/inmuebles-venta-posadas.html" if pag==1
               else f"https://www.zonaprop.com.ar/inmuebles-venta-posadas-pagina-{pag}.html")
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: break
            html = r.text
        except Exception as e: log.warning(f"ZonaProp p{pag}: {e}"); break
        arts = re.findall(r'<article[^>]+data-id=["\']?(\d+)["\']?[^>]*>(.*?)</article>',
                          html, re.DOTALL|re.IGNORECASE)
        if not arts: break
        for aid, ahtml in arts:
            try:
                tm = re.search(r'<h2[^>]*>(.*?)</h2>', ahtml, re.DOTALL)
                titulo = strip(tm.group(1)) if tm else f"ZP-{aid}"
                pm = re.search(r'(USD?|US\$|\$)\s*([\d\.,]+)', ahtml)
                precio,moneda = 0,"USD"
                if pm:
                    moneda = "USD" if "U" in pm.group(1).upper() else "ARS"
                    precio = int(pm.group(2).replace(".","").replace(",",""))
                dm  = re.search(r'class=["\'][^"\']*address[^"\']*["\'][^>]*>(.*?)</', ahtml, re.DOTALL)
                dir_ = strip(dm.group(1)) if dm else "Posadas"
                sm   = re.search(r'(\d+)\s*m[²2]', ahtml)
                drm  = re.search(r'(\d+)\s*(?:dorm|amb)', ahtml, re.IGNORECASE)
                um   = re.search(r'href=["\'](/propiedades/[^"\']+)["\']', ahtml)
                url_p = "https://www.zonaprop.com.ar" + um.group(1) if um else ""
                # Foto: og:image ya no está en listing; usar data-flickity o cdn
                foto_m = re.findall(r'(?:data-flickity-lazyload|data-src)=["\']'
                                    r'(https://[^"\']+\.(?:jpg|jpeg|webp)[^"\']*)["\']', ahtml, re.IGNORECASE)
                fotos = list(dict.fromkeys(foto_m))[:6]
                barrio = barrio_de(dir_+" "+titulo)
                lat,lng = geocode_fallback(barrio)
                if precio <= 0: continue
                res.append(mk_prop(
                    id=pid("zp",aid), ref=f"ZP-{aid}", fuente="ZonaProp",
                    tipo=tipo_de(titulo+" "+dir_), titulo=titulo, barrio=barrio,
                    direccion=dir_, precio=precio, moneda=moneda,
                    m2=num(sm.group(1)) if sm else None,
                    dorms=num(drm.group(1)) if drm else None,
                    desc=titulo, fotos=fotos, url=url_p,
                    inm="ZonaProp", lat=lat, lng=lng,
                ))
            except: continue
        await asyncio.sleep(DELAY)
    log.info(f"ZonaProp: {len(res)}")
    return res

# ── ARGENPROP ──────────────────────────────────────────────────────────────
async def scrape_argenprop(client):
    res = []
    for pag in range(1, MAX_PAGES+1):
        url = f"https://www.argenprop.com/inmuebles-en-venta-en-posadas?pagina={pag}"
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: break
            html = r.text
        except Exception as e: log.warning(f"Argenprop p{pag}: {e}"); break
        cards = re.findall(r'<div[^>]+listing__item[^>]*>(.*?)</div>\s*</div>',
                           html, re.DOTALL|re.IGNORECASE)
        if not cards: break
        for j,card in enumerate(cards):
            try:
                tm  = re.search(r'listing__title[^"\']*["\'][^>]*>(.*?)</', card, re.DOTALL)
                titulo = strip(tm.group(1)) if tm else f"AP-{j}"
                pm  = re.search(r'(USD?|US\$|\$)\s*([\d\.,]+)', card)
                precio,moneda = 0,"USD"
                if pm:
                    moneda = "USD" if "U" in pm.group(1).upper() else "ARS"
                    precio = int(pm.group(2).replace(".","").replace(",",""))
                dm  = re.search(r'listing__location[^"\']*["\'][^>]*>(.*?)</', card, re.DOTALL)
                dir_ = strip(dm.group(1)) if dm else "Posadas"
                sm   = re.search(r'(\d+)\s*m[²2]', card)
                drm  = re.search(r'(\d+)\s*dorm', card, re.IGNORECASE)
                um   = re.search(r'href=["\']([^"\']+propiedad[^"\']+)["\']', card)
                url_p = "https://www.argenprop.com" + um.group(1) if um else ""
                foto_m = re.findall(r'(?:data-src|data-lazy|src)=["\']'
                                    r'(https://[^"\']+\.(?:jpg|jpeg|webp)[^"\']*)["\']', card, re.IGNORECASE)
                fotos = list(dict.fromkeys(foto_m))[:6]
                barrio = barrio_de(dir_+" "+titulo)
                lat,lng = geocode_fallback(barrio)
                if precio <= 0: continue
                res.append(mk_prop(
                    id=pid("ap",f"{j}{titulo[:15]}"), ref=f"AP-{j}",
                    fuente="Argenprop", tipo=tipo_de(titulo+" "+dir_),
                    titulo=titulo, barrio=barrio, direccion=dir_,
                    precio=precio, moneda=moneda,
                    m2=num(sm.group(1)) if sm else None,
                    dorms=num(drm.group(1)) if drm else None,
                    desc=titulo, fotos=fotos, url=url_p, inm="Argenprop",
                    lat=lat, lng=lng,
                ))
            except: continue
        await asyncio.sleep(DELAY)
    log.info(f"Argenprop: {len(res)}")
    return res

# ── TORRES INMOBILIARIA ────────────────────────────────────────────────────
async def scrape_torres(client):
    """
    Torres: raspa el listado para obtener IDs, luego visita cada ficha
    para obtener fotos reales (/sistema/fotos/NNNN.jpg) y coords exactas de Google Maps.
    """
    res = []
    # Paso 1: obtener todos los IDs del listado
    ids = set()
    for pag in range(1, MAX_PAGES+1):
        url = (f"https://inmobiliariatorres.com.ar/2023/properties-grid-3/index.php"
               f"?tipo_operacion=venta&pagina={pag}")
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: break
            html = r.text
        except: break
        nuevos = set(re.findall(r'ficha\.php\?id=(\d+)', html))
        if not nuevos or nuevos.issubset(ids): break
        ids.update(nuevos)
        await asyncio.sleep(DELAY)
    log.info(f"Torres listado: {len(ids)} fichas encontradas")

    # Paso 2: visitar cada ficha y extraer datos completos
    for fid in list(ids)[:80]:  # máximo 80 fichas para no sobrecargar
        url_ficha = f"https://inmobiliariatorres.com.ar/2023/ficha.php?id={fid}"
        try:
            r = await client.get(url_ficha, headers=HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200: continue
            html = r.text
        except: continue

        try:
            # Título (h1)
            tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
            titulo = strip(tm.group(1)) if tm else f"Torres-{fid}"

            # Precio
            pm = re.search(r'\$([\d\.,]+)', html)
            um_usd = re.search(r'USD\s*([\d\.,]+)', html, re.IGNORECASE)
            if um_usd:
                precio = int(um_usd.group(1).replace(".","").replace(",",""))
                moneda = "USD"
            elif pm:
                precio = int(pm.group(1).replace(".","").replace(",",""))
                moneda = "ARS"
            else: continue

            # Tipo
            tipo_m = re.search(r'\[(Casas|Departamentos|Terrenos|Locales|Oficinas|Duplex)\]', html)
            tipo_map = {"Casas":"Casa","Departamentos":"Departamento","Terrenos":"Terreno",
                        "Locales":"Local","Oficinas":"Local","Duplex":"Duplex"}
            tipo = tipo_map.get(tipo_m.group(1),"Casa") if tipo_m else tipo_de(titulo)

            # Dormitorios / baños / m²
            dm  = re.search(r'Dormitorios:\s*(\d+)', html)
            bm  = re.search(r'Baños:\s*(\d+)', html)
            sm  = re.search(r'Superficie\s*:\s*([\d.]+)\s*m2', html)
            fm  = re.search(r'Frente:\s*([\d.]+)', html)

            # Dirección y coordenadas
            dir_m = re.search(r'\[([^\]]+)\]\(https://maps\.google\.com[^\)]+q=([^,&\)]+),([^&\)]+)', html)
            if dir_m:
                direccion = dir_m.group(1).strip()
                try: lat,lng = float(dir_m.group(2)), float(dir_m.group(3))
                except: lat,lng = geocode_fallback(titulo)
            else:
                # segundo patrón: q=-27.xxx,-55.xxx
                coords = parse_gmaps_coords(html)
                lat,lng = coords if coords else geocode_fallback(titulo)
                dir_m2 = re.search(r'\[([^\]]{5,60})\]\(https://maps\.google', html)
                direccion = dir_m2.group(1).strip() if dir_m2 else "Posadas"

            barrio = barrio_de(direccion + " " + titulo)

            # Fotos reales: /sistema/fotos/NNNNN.jpg
            fotos = [f"https://inmobiliariatorres.com.ar/sistema/fotos/{f}.jpg"
                     for f in re.findall(r'/sistema/fotos/(\d+)\.jpg', html)]
            if not fotos:
                fotos = og_image(html)

            # Descripción
            desc_m = re.search(r'### Descripción\s*\n+(.*?)(?=###|\Z)', html, re.DOTALL)
            desc = strip(desc_m.group(1))[:400] if desc_m else titulo

            if precio <= 0: continue
            res.append(mk_prop(
                id=pid("torres",fid), ref=f"TORRES-{fid}",
                fuente="Torres Inmobiliaria", tipo=tipo,
                titulo=titulo.title(), barrio=barrio, direccion=direccion,
                precio=precio, moneda=moneda,
                m2=int(float(sm.group(1))) if sm else None,
                dorms=int(dm.group(1)) if dm else None,
                banos=int(bm.group(1)) if bm else None,
                desc=desc, fotos=fotos[:8], url=url_ficha,
                inm="Torres Inmobiliaria",
                email="info@inmobiliariatorres.com.ar", tel="+543764533092",
                lat=round(lat,6), lng=round(lng,6),
            ))
        except: continue
        await asyncio.sleep(DELAY)

    log.info(f"Torres Inmobiliaria: {len(res)}")
    return res

# ── DAVIÑA INMOBILIARIA ────────────────────────────────────────────────────
async def scrape_davina(client):
    """
    Daviña: listado WordPress con 6 páginas (~61 props).
    Visita cada ficha /property/slug/ para obtener fotos reales y dirección exacta.
    """
    res = []
    slugs = []
    for pag in range(1, 8):
        url = "https://davinia.com.ar/" if pag==1 else f"https://davinia.com.ar/?paged-1={pag}"
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: break
            html = r.text
        except Exception as e: log.warning(f"Daviña p{pag}: {e}"); break
        nuevos = re.findall(r'https://davinia\.com\.ar/property/([a-z0-9\-]+)/', html)
        nuevos = list(dict.fromkeys(nuevos))
        if not nuevos: break
        slugs.extend([s for s in nuevos if s not in slugs])
        await asyncio.sleep(DELAY)

    log.info(f"Daviña listado: {len(slugs)} slugs")

    for slug in slugs[:80]:
        url_ficha = f"https://davinia.com.ar/property/{slug}/"
        try:
            r = await client.get(url_ficha, headers=HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200: continue
            html = r.text
        except: continue
        try:
            # Título
            tm = re.search(r'# ([^\n]+)\n', html)
            titulo = tm.group(1).strip() if tm else slug.replace("-"," ").title()

            # Precio: "USD 225,000" o "$ 27000000"
            pm_usd = re.search(r'USD\s*([\d,\.]+)', html)
            pm_ars = re.search(r'\$\s*([\d\.]+)', html)
            if pm_usd:
                precio = int(pm_usd.group(1).replace(",","").replace(".",""))
                moneda = "USD"
            elif pm_ars:
                precio = int(pm_ars.group(1).replace(".",""))
                moneda = "ARS"
            else: continue
            if precio <= 0: continue

            # Dirección (línea que contiene "Posadas" o "Misiones")
            dir_m = re.search(r'([^\n]{10,80}(?:Posadas|Misiones|N330\d)[^\n]*)', html)
            direccion = dir_m.group(1).strip() if dir_m else "Posadas, Misiones"

            # Coordenadas desde Google Maps embed si está, si no Nominatim
            coords = parse_gmaps_coords(html)
            if not coords:
                coords = await geocode_nominatim(client, direccion)
            lat,lng = coords if coords else geocode_fallback(barrio_de(direccion+" "+titulo))

            barrio = barrio_de(direccion + " " + titulo)

            # Características
            dm  = re.search(r'\*\*(\d+)\*\*\s*camas', html)
            bm  = re.search(r'\*\*(\d+)\*\*\s*ba(?:ño|lneario)', html, re.IGNORECASE)
            sm  = re.search(r'\*\*([\d.]+)\*\*\s*m²', html)

            # Fotos: wp-content/uploads (sin thumbnail -300x135)
            fotos_raw = re.findall(
                r'https://davinia\.com\.ar/wp-content/uploads/[\d/]+[^"\')\s]+\.(?:jpg|jpeg|webp|png)',
                html
            )
            # Filtrar thumbnails (contienen NxN en el nombre)
            fotos = list(dict.fromkeys([
                f for f in fotos_raw if not re.search(r'-\d+x\d+\.', f)
            ]))[:10]
            if not fotos:
                fotos = og_image(html)

            # Descripción
            desc_m = re.search(r'### Descripción\s*\n+(.*?)(?=###|\Z)', html, re.DOTALL)
            desc = strip(desc_m.group(1))[:400] if desc_m else titulo

            res.append(mk_prop(
                id=pid("davina",slug), ref=slug,
                fuente="Daviña Inmobiliaria", tipo=tipo_de(titulo),
                titulo=titulo, barrio=barrio, direccion=direccion,
                precio=precio, moneda=moneda,
                m2=int(float(sm.group(1))) if sm else None,
                dorms=int(dm.group(1)) if dm else None,
                banos=int(bm.group(1)) if bm else None,
                desc=desc, fotos=fotos, url=url_ficha,
                inm="Daviña Inmobiliaria",
                email="davinainmobiliaria@gmail.com", tel="+5493764425983",
                lat=round(lat,6), lng=round(lng,6),
            ))
        except: continue
        await asyncio.sleep(DELAY)

    log.info(f"Daviña Inmobiliaria: {len(res)}")
    return res

# ── SOSA INMOBILIARIA ──────────────────────────────────────────────────────
async def scrape_sosa(client):
    """
    Sosa: CMS Xintel. Listado en /propiedades.php?ope=V&p=N (pág 1-N de 5 props).
    Luego visita cada ficha para obtener og:image y descripción completa.
    """
    res = []
    fichas = []
    for pag in range(0, MAX_PAGES):
        url = f"https://www.sosainmobiliaria.com/propiedades.php?ope=V&p={pag}"
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: break
            html = r.text
        except Exception as e: log.warning(f"Sosa p{pag}: {e}"); break
        # Cada tarjeta: link al slug + precio + ubicación
        cards = re.findall(
            r'\(https://www\.sosainmobiliaria\.com/([^)]+)\)\s*\nVenta\s*\n+'
            r'### \[([^\]]+)\]\([^)]+\)\s*\n+([^\n]+)\s*\n+'
            r'(\$\s*[\d\.]+)',
            html
        )
        if not cards: break
        for slug, titulo, ubicacion, precio_raw in cards:
            precio = int(re.sub(r'[^\d]', '', precio_raw))
            if precio > 0:
                fichas.append((slug, titulo, ubicacion, precio))
        await asyncio.sleep(DELAY)

    log.info(f"Sosa listado: {len(fichas)} fichas")

    for slug, titulo_base, ubicacion, precio_base in fichas[:80]:
        url_ficha = f"https://www.sosainmobiliaria.com/{slug}"
        try:
            r = await client.get(url_ficha, headers=HEADERS, timeout=20, follow_redirects=True)
            if r.status_code != 200:
                # Sin ficha: usar datos del listado
                barrio = barrio_de(ubicacion + " " + titulo_base)
                lat,lng = geocode_fallback(barrio)
                res.append(mk_prop(
                    id=pid("sosa",slug), ref=slug,
                    fuente="Sosa Inmobiliaria", tipo=tipo_de(titulo_base),
                    titulo=(titulo_base + " " + ubicacion)[:80], barrio=barrio,
                    direccion=ubicacion, precio=precio_base, moneda="ARS",
                    desc=titulo_base,
                    url=url_ficha, inm="Sosa Inmobiliaria", tel="+5493764422483",
                    lat=lat, lng=lng,
                ))
                continue
            html = r.text
        except:
            continue

        try:
            # Título de la ficha (más completo)
            tm = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
            titulo = strip(tm.group(1)) if tm else titulo_base

            # Precio (puede ser USD en la ficha)
            pm_usd = re.search(r'USD\s*([\d\.,]+)', html, re.IGNORECASE)
            pm_ars = re.search(r'\$\s*([\d\.]+)', html)
            if pm_usd:
                precio = int(pm_usd.group(1).replace(",","").replace(".",""))
                moneda = "USD"
            else:
                precio = precio_base
                moneda = "ARS"

            # Dirección desde contenido
            dir_m = re.search(r'(?:ubicad[ao]|calle|av\.|avenida|en\s)([^<\n]{10,80}(?:posadas|barrio|villa)[^<\n]{0,60})', html, re.IGNORECASE)
            direccion = strip(dir_m.group(1)) if dir_m else ubicacion

            # Coords: Nominatim con la dirección
            coords = await geocode_nominatim(client, direccion)
            barrio = barrio_de(direccion + " " + titulo)
            lat,lng = coords if coords else geocode_fallback(barrio)

            dm = re.search(r'(\d+)\s*dormitorio', html, re.IGNORECASE)
            bm = re.search(r'(\d+)\s*ba[ñn]o', html, re.IGNORECASE)
            sm = re.search(r'(\d+)\s*m(?:etros|²|2)\s*(?:cuadrados)?', html, re.IGNORECASE)

            # Foto principal: meta og:image
            fotos = og_image(html)

            # Descripción
            desc_m = re.search(r'og:description["\'][^"\']*["\']([^"\']+)', html)
            desc = strip(desc_m.group(1))[:400] if desc_m else titulo

            res.append(mk_prop(
                id=pid("sosa",slug), ref=slug,
                fuente="Sosa Inmobiliaria", tipo=tipo_de(titulo),
                titulo=titulo[:80], barrio=barrio, direccion=direccion,
                precio=precio, moneda=moneda,
                m2=num(sm.group(1)) if sm else None,
                dorms=num(dm.group(1)) if dm else None,
                banos=num(bm.group(1)) if bm else None,
                desc=desc, fotos=fotos, url=url_ficha,
                inm="Sosa Inmobiliaria", tel="+5493764422483",
                lat=round(lat,6), lng=round(lng,6),
            ))
        except: continue
        await asyncio.sleep(DELAY)

    log.info(f"Sosa Inmobiliaria: {len(res)}")
    return res

# ── ZAPANI (Tokko Broker) ──────────────────────────────────────────────────
async def scrape_zapani(client):
    res = []
    endpoints = [
        ("https://www.inmobiliariazapani.com.ar/Venta", ""),
        ("https://www.inmobiliariazapani.com.ar/Venta?page=2", ""),
        ("https://www.inmobiliariazapani.com.ar/Venta?page=3", ""),
    ]
    for url,_ in endpoints:
        try:
            r = await client.get(url, headers=HEADERS, timeout=25, follow_redirects=True)
            if r.status_code != 200: continue
            html = r.text
        except Exception as e: log.warning(f"Zapani: {e}"); continue

        # Tokko Broker expone items en JSON dentro del HTML
        json_m = re.search(r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*;', html, re.DOTALL)
        if json_m:
            try:
                state = json.loads(json_m.group(1))
                props_raw = (state.get("properties",{}).get("listData",{}).get("objects") or
                             state.get("listing",{}).get("results",[]))
                for p in props_raw:
                    precio_obj = (p.get("operations",[{}]) or [{}])[0]
                    precio = int(precio_obj.get("prices",[{}])[0].get("price",0) or 0)
                    moneda = precio_obj.get("prices",[{}])[0].get("currency","USD")
                    lat = p.get("geo",{}).get("lat") or p.get("real_address",{}).get("lat")
                    lng = p.get("geo",{}).get("lon") or p.get("real_address",{}).get("lon")
                    if not lat or not lng: lat,lng = geocode_fallback(p.get("address","Posadas"))
                    fotos = [f.get("image","") for f in p.get("photos",[])[:8] if f.get("image")]
                    res.append(mk_prop(
                        id=pid("zapani",str(p.get("id",""))), ref=str(p.get("id","")),
                        fuente="Zapani Inmobiliaria",
                        tipo=tipo_de(p.get("type",{}).get("name","")),
                        titulo=p.get("address","Propiedad Zapani"),
                        barrio=barrio_de(p.get("address","")),
                        direccion=p.get("address","Posadas"),
                        precio=precio, moneda=moneda,
                        m2=num(p.get("total_surface")),
                        m2c=num(p.get("roofed_surface")),
                        dorms=num(p.get("room_amount")),
                        banos=num(p.get("bathroom_amount")),
                        desc=strip(p.get("description",""))[:400],
                        fotos=fotos, url=f"https://www.inmobiliariazapani.com.ar{p.get('url','')}",
                        inm="Zapani Inmobiliaria",
                        email="inmobiliariazapani@hotmail.com", tel="+5493764436113",
                        lat=round(float(lat),6), lng=round(float(lng),6),
                    ))
                continue
            except: pass

        # Fallback: scraping HTML genérico de Tokko Broker
        items = re.findall(
            r'href=["\']/(propiedad[^"\']+)["\'][^>]*>.*?'
            r'(USD?|US\$|\$)\s*([\d\.,]+).*?'
            r'class=["\'][^"\']*card[^"\']*title[^"\']*["\'][^>]*>(.*?)<',
            html, re.DOTALL|re.IGNORECASE
        )
        for slug, mon_raw, precio_str, titulo in items[:30]:
            try:
                precio = int(precio_str.replace(".","").replace(",",""))
                moneda = "USD" if "U" in mon_raw.upper() else "ARS"
                titulo = strip(titulo)
                barrio = barrio_de(titulo)
                lat,lng = geocode_fallback(barrio)
                res.append(mk_prop(
                    id=pid("zapani",slug), ref=slug, fuente="Zapani Inmobiliaria",
                    tipo=tipo_de(titulo), titulo=titulo, barrio=barrio,
                    direccion=barrio+", Posadas", precio=precio, moneda=moneda,
                    desc=titulo, url=f"https://www.inmobiliariazapani.com.ar/{slug}",
                    inm="Zapani Inmobiliaria",
                    email="inmobiliariazapani@hotmail.com", tel="+5493764436113",
                    lat=lat, lng=lng,
                ))
            except: continue
        await asyncio.sleep(DELAY)

    log.info(f"Zapani Inmobiliaria: {len(res)}")
    return res

# ── FÉNIX + OTRAS LOCALES ──────────────────────────────────────────────────
async def scrape_locales(client):
    res = []
    fuentes = [
        {"n":"Fénix Inmobiliaria","url":"https://fenixxweb.com/venta",
         "email":"info@fenixxweb.com","tel":"+5493764555111"},
        {"n":"Costa Remates","url":"https://costarematesycorretaje.com.ar/propiedades/venta",
         "email":"info@costarematesycorretaje.com.ar","tel":"+5493764222333"},
        {"n":"Origen Propiedades","url":"https://origen-propiedades.com/propiedades",
         "email":"info@origen-propiedades.com","tel":"+5493764333444"},
    ]
    for f in fuentes:
        for pag in range(1, 4):
            url = f["url"] if pag==1 else f"{f['url']}?page={pag}"
            try:
                r = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
                if r.status_code != 200: break
                html = r.text
            except Exception as e:
                log.warning(f"{f['n']} p{pag}: {e}"); break

            precios = re.findall(r'U[SD$]+\s*([\d\.,]{4,})', html)
            titulos = [strip(t) for t in re.findall(r'<h[123][^>]*>(.*?)</h[123]>',html,re.DOTALL|re.IGNORECASE)
                       if len(strip(t)) > 8]
            imgs = re.findall(
                r'(?:data-src|data-lazy|src)=["\']'
                r'(https?://[^"\']+\.(?:jpg|jpeg|webp|png)(?:\?[^"\']*)?)["\']', html, re.IGNORECASE
            )
            imgs = [u for u in imgs if not re.search(r'logo|icon|favicon|sprite', u, re.IGNORECASE)]

            for i, precio_str in enumerate(precios[:20]):
                try:
                    precio = int(precio_str.replace(".","").replace(",",""))
                    if precio < 1000: continue
                    titulo = titulos[i] if i < len(titulos) else f"Propiedad {f['n']}"
                    barrio = barrio_de(titulo)
                    lat,lng = geocode_fallback(barrio)
                    fotos = imgs[i*2:i*2+2] if imgs else []
                    res.append(mk_prop(
                        id=pid(f["n"],f"{precio}{titulo[:15]}"),
                        ref=f"{f['n'][:3].upper()}-{i}-{pag}",
                        fuente=f["n"], tipo=tipo_de(titulo),
                        titulo=titulo, barrio=barrio,
                        direccion=barrio+", Posadas",
                        precio=precio, moneda="USD",
                        desc=titulo, fotos=fotos,
                        url=f["url"], inm=f["n"],
                        email=f["email"], tel=f["tel"],
                        lat=lat, lng=lng,
                    ))
                except: continue
            if not precios: break
            await asyncio.sleep(DELAY)
    log.info(f"Locales: {len(res)}")
    return res

# ── DATOS SEMILLA ──────────────────────────────────────────────────────────
SEMILLA_RAW = [
  # CENTRO
  {"tipo":"Departamento","titulo":"Dpto 2 amb. frente al río con balcón","barrio":"Centro","direccion":"Av. Costanera 850","precio":68000,"moneda":"USD","m2_total":52,"dormitorios":1,"banos":1,"desc":"Luminoso departamento frente al río Paraná, balcón con vista panorámica.","inm":"Posadas Propiedades","email":"info@posadasprops.com.ar","tel":"+5493764123456","url":"https://www.posadasprops.com.ar","fotos":[],"nuevo":True},
  {"tipo":"PH","titulo":"PH 3 amb. con patio — Centro histórico","barrio":"Centro","direccion":"Calle Bolívar 340","precio":55000,"moneda":"USD","m2_total":75,"dormitorios":2,"banos":1,"desc":"PH en planta baja con patio privado de 40m², cocina renovada.","inm":"Misiones Inmuebles","email":"ventas@misionesinmuebles.com.ar","tel":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","fotos":[],"nuevo":False},
  {"tipo":"Local","titulo":"Local comercial céntrico 80m² sobre San Martín","barrio":"Centro","direccion":"Av. San Martín 1200","precio":90000,"moneda":"USD","m2_total":80,"dormitorios":0,"banos":1,"desc":"Excelente ubicación, planta libre, galería de mucho tránsito peatonal.","inm":"BienInvertido Posadas","email":"info@bieninvertido.com","tel":"+5493764987654","url":"https://www.bieninvertido.com","fotos":[],"nuevo":True},
  {"tipo":"Casa","titulo":"Casa antigua con terreno 600m² — Centro","barrio":"Centro","direccion":"Calle Córdoba 780","precio":125000,"moneda":"USD","m2_total":150,"dormitorios":3,"banos":2,"desc":"Casa antigua en lote de 600m², ideal para proyecto. Ubicación excelente.","inm":"Fénix Inmobiliaria","email":"info@fenixposadas.com.ar","tel":"+5493764555111","url":"https://fenixxweb.com","fotos":[],"nuevo":False},
  # BARRIO NORTE
  {"tipo":"Casa","titulo":"Casa 4 dorms. con pileta — Barrio Norte","barrio":"Barrio Norte","direccion":"Calle Andresito 450","precio":145000,"moneda":"USD","m2_total":210,"dormitorios":4,"banos":3,"desc":"Hermosa casa, 3 dormitorios en suite, pileta, asador, garage doble.","inm":"Posadas Propiedades","email":"info@posadasprops.com.ar","tel":"+5493764123456","url":"https://www.posadasprops.com.ar","fotos":[],"nuevo":False},
  {"tipo":"Departamento","titulo":"Dpto 3 amb. con amenities — Torre Norte","barrio":"Barrio Norte","direccion":"Av. Uruguay 2100","precio":82000,"moneda":"USD","m2_total":78,"dormitorios":2,"banos":2,"desc":"Moderno departamento en torre premium, SUM, piscina, seguridad 24h.","inm":"Crest Inmobiliaria","email":"ventas@crestposadas.com.ar","tel":"+5493764112233","url":"https://www.crestposadas.com.ar","fotos":[],"nuevo":True},
  {"tipo":"Terreno","titulo":"Lote 600m² en barrio privado con todos los servicios","barrio":"Barrio Norte","direccion":"Barrio Los Aromos, Lote 12","precio":48000,"moneda":"USD","m2_total":600,"dormitorios":0,"banos":0,"desc":"Lote en barrio privado con seguridad 24hs. Todos los servicios.","inm":"Misiones Inmuebles","email":"ventas@misionesinmuebles.com.ar","tel":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","fotos":[],"nuevo":False},
  # COSTANERA
  {"tipo":"Casa","titulo":"Casa frente al río con embarcadero — Costanera","barrio":"Costanera","direccion":"Av. Costanera 1800","precio":195000,"moneda":"USD","m2_total":280,"dormitorios":4,"banos":3,"desc":"Majestuosa casa con acceso directo al río, embarcadero, piscina, quincho.","inm":"Posadas Propiedades","email":"info@posadasprops.com.ar","tel":"+5493764123456","url":"https://www.posadasprops.com.ar","fotos":[],"nuevo":False},
  {"tipo":"Departamento","titulo":"Studio amoblado vista al Paraná","barrio":"Costanera","direccion":"Calle F. L. Beltrán 22","precio":42000,"moneda":"USD","m2_total":38,"dormitorios":1,"banos":1,"desc":"Studio moderno completamente equipado, ideal inversión turística.","inm":"InvertiMisiones","email":"inversiones@invertimisiones.com.ar","tel":"+5493764445566","url":"https://www.invertimisiones.com.ar","fotos":[],"nuevo":True},
  # VILLA URQUIZA
  {"tipo":"Casa","titulo":"Casa 3 dorms. en barrio tranquilo — Villa Urquiza","barrio":"Villa Urquiza","direccion":"Calle Yapeyú 760","precio":95000,"moneda":"USD","m2_total":160,"dormitorios":3,"banos":2,"desc":"Casa familiar con patio y jardín, cocina amplia, lavadero, garage.","inm":"BienInvertido Posadas","email":"info@bieninvertido.com","tel":"+5493764987654","url":"https://www.bieninvertido.com","fotos":[],"nuevo":False},
  {"tipo":"Duplex","titulo":"Dúplex moderno 2 dorms. + estudio","barrio":"Villa Urquiza","direccion":"Pasaje Las Misiones 123","precio":115000,"moneda":"USD","m2_total":140,"dormitorios":3,"banos":2,"desc":"Dúplex contemporáneo, pileta y SUM compartidos.","inm":"Crest Inmobiliaria","email":"ventas@crestposadas.com.ar","tel":"+5493764112233","url":"https://www.crestposadas.com.ar","fotos":[],"nuevo":True},
  # PEÑAFLOR
  {"tipo":"Casa","titulo":"Chalet amplio con garage — Bº Peñaflor","barrio":"Peñaflor","direccion":"Calle Tucumán 890","precio":120000,"moneda":"USD","m2_total":200,"dormitorios":3,"banos":2,"desc":"Chalet estilo colonial, materiales de primera, jardín, garage doble.","inm":"Misiones Inmuebles","email":"ventas@misionesinmuebles.com.ar","tel":"+5493764654321","url":"https://www.misionesinmuebles.com.ar","fotos":[],"nuevo":False},
  # JARDÍN AMÉRICA
  {"tipo":"Departamento","titulo":"Dpto 2 dorms. en edificio moderno","barrio":"Jardín América","direccion":"Calle Salta 1500","precio":72000,"moneda":"USD","m2_total":68,"dormitorios":2,"banos":1,"desc":"Departamento nuevo con cochera, balcón terraza, edificio sustentable.","inm":"Crest Inmobiliaria","email":"ventas@crestposadas.com.ar","tel":"+5493764112233","url":"https://www.crestposadas.com.ar","fotos":[],"nuevo":True},
  {"tipo":"Casa","titulo":"Casa 5 dorms. zona universitaria","barrio":"Jardín América","direccion":"Calle Rioja 2200","precio":165000,"moneda":"USD","m2_total":250,"dormitorios":5,"banos":3,"desc":"Gran propiedad para familia numerosa, cerca de la UNaM. Patio y asador.","inm":"Posadas Propiedades","email":"info@posadasprops.com.ar","tel":"+5493764123456","url":"https://www.posadasprops.com.ar","fotos":[],"nuevo":False},
  # ZAIMÁN
  {"tipo":"Duplex","titulo":"Dúplex premium — Bº Zaimán country","barrio":"Zaimán","direccion":"Barrio Zaimán, Lote 45","precio":210000,"moneda":"USD","m2_total":300,"dormitorios":4,"banos":4,"desc":"Dúplex de lujo en country con laguna, piscina privada, quincho, smart home.","inm":"BienInvertido Posadas","email":"info@bieninvertido.com","tel":"+5493764987654","url":"https://www.bieninvertido.com","fotos":[],"nuevo":True},
  {"tipo":"Terreno","titulo":"Lote premium country Zaimán 800m²","barrio":"Zaimán","direccion":"Barrio Zaimán, Sector B","precio":85000,"moneda":"USD","m2_total":800,"dormitorios":0,"banos":0,"desc":"Lote en country con laguna, seguridad 24hs, escritura al día.","inm":"InvertiMisiones","email":"inversiones@invertimisiones.com.ar","tel":"+5493764445566","url":"https://www.invertimisiones.com.ar","fotos":[],"nuevo":False},
  # SOLARI
  {"tipo":"Departamento","titulo":"Dpto 2 dorms. a estrenar — Edificio Torre Sol","barrio":"Centro","direccion":"Colón 1350","precio":89000,"moneda":"USD","m2_total":65,"dormitorios":2,"banos":2,"desc":"A estrenar, cochera, amenities completos, balcón con vista a la ciudad.","inm":"Solari Bienes Raíces","email":"info@solaribienesraices.com.ar","tel":"+5493764439998","url":"https://www.facebook.com/solari.bienesraices/","fotos":[],"nuevo":True},
  {"tipo":"Casa","titulo":"Casa 3 dorms. con cochera doble — Villa Cabello","barrio":"Villa Cabello","direccion":"Alvear 2890","precio":135000,"moneda":"USD","m2_total":190,"dormitorios":3,"banos":2,"desc":"Casa de categoría con jardín, cochera doble, dependencia de servicio.","inm":"Solari Bienes Raíces","email":"info@solaribienesraices.com.ar","tel":"+5493764439998","url":"https://www.facebook.com/solari.bienesraices/","fotos":[],"nuevo":False},
  {"tipo":"Terreno","titulo":"Lote inversión 350m² — Itaembé Guazú","barrio":"Itaembé Guazú","direccion":"Calle 161 esquina 24","precio":24500,"moneda":"USD","m2_total":350,"dormitorios":0,"banos":0,"desc":"Excelente oportunidad de inversión, lote nivelado con todos los servicios.","inm":"Solari Bienes Raíces","email":"info@solaribienesraices.com.ar","tel":"+5493764439998","url":"https://www.facebook.com/solari.bienesraices/","fotos":[],"nuevo":True},
  # TORRES
  {"tipo":"Departamento","titulo":"Edificio Victoria Regia — 2 dorms. con vista","barrio":"Centro","direccion":"Alvear N° 1977","precio":171130,"moneda":"USD","m2_total":85,"dormitorios":2,"banos":2,"desc":"Edificio premium, balcón, cochera, amenities, excelente ubicación céntrica.","inm":"Torres Inmobiliaria","email":"info@inmobiliariatorres.com.ar","tel":"+543764533092","url":"https://inmobiliariatorres.com.ar","fotos":["https://inmobiliariatorres.com.ar/fotos_portada/22030.jpg"],"nuevo":False},
  {"tipo":"Casa","titulo":"Hermosa casa de 5 dormitorios — Av. Centenario","barrio":"Posadas","direccion":"Av Centenario N° 2954","precio":280000,"moneda":"USD","m2_total":310,"dormitorios":5,"banos":4,"desc":"Amplia propiedad familiar, doble cochera, parque, pileta y quincho.","inm":"Torres Inmobiliaria","email":"info@inmobiliariatorres.com.ar","tel":"+543764533092","url":"https://inmobiliariatorres.com.ar","fotos":[],"nuevo":True},
  {"tipo":"Terreno","titulo":"Terreno cerca de la costanera — 510m²","barrio":"Costanera","direccion":"Av. López Torres","precio":220000,"moneda":"USD","m2_total":510,"dormitorios":0,"banos":0,"desc":"Excelente terreno a metros de la costanera, ideal desarrollo de categoría.","inm":"Torres Inmobiliaria","email":"info@inmobiliariatorres.com.ar","tel":"+543764533092","url":"https://inmobiliariatorres.com.ar","fotos":[],"nuevo":False},
  # DAVIÑA
  {"tipo":"Departamento","titulo":"Departamento exclusivo en Costanera de Posadas","barrio":"Costanera","direccion":"Av. Roque Sáenz Peña 2300","precio":225000,"moneda":"USD","m2_total":125,"dormitorios":2,"banos":3,"desc":"A 200 metros del río Paraná, piso exclusivo con vista, 2 dorms. en suite.","inm":"Daviña Inmobiliaria","email":"davinainmobiliaria@gmail.com","tel":"+5493764425983","url":"https://davinia.com.ar","fotos":["https://davinia.com.ar/wp-content/uploads/2026/01/Departamento-exclusivo-en-Costanera-de-Posadas-14.jpeg"],"nuevo":True},
  {"tipo":"Casa","titulo":"Casa de 3 plantas — Barrio Villa Sarita","barrio":"Villa Sarita","direccion":"Hernández 2473","precio":195000,"moneda":"USD","m2_total":428,"dormitorios":4,"banos":3,"desc":"Propiedad de gran categoría en tres plantas, barrio histórico de Villa Sarita.","inm":"Daviña Inmobiliaria","email":"davinainmobiliaria@gmail.com","tel":"+5493764425983","url":"https://davinia.com.ar","fotos":[],"nuevo":False},
  {"tipo":"Terreno","titulo":"Lote en esquina — Av. Cabred, Villa Urquiza","barrio":"Villa Urquiza","direccion":"Av. Domingo Cabred & Perito Moreno","precio":110000,"moneda":"USD","m2_total":300,"dormitorios":0,"banos":0,"desc":"Esquina con potencial constructivo en zona de excelente categoría.","inm":"Daviña Inmobiliaria","email":"davinainmobiliaria@gmail.com","tel":"+5493764425983","url":"https://davinia.com.ar","fotos":[],"nuevo":True},
  # SOSA
  {"tipo":"Casa","titulo":"Casa 3 dormitorios — esquina Monseñor de Andrea","barrio":"Posadas","direccion":"Av. Monseñor de Andrea y Calle 122","precio":680000000,"moneda":"ARS","m2_total":180,"dormitorios":3,"banos":3,"desc":"Propiedad de lujo en esquina, dormitorio principal con baño en suite.","inm":"Sosa Inmobiliaria","email":"","tel":"+5493764422483","url":"https://www.sosainmobiliaria.com","fotos":[],"nuevo":True},
  {"tipo":"Casa","titulo":"Casa en piedra — Villa Sarita","barrio":"Villa Sarita","direccion":"Coronel Álvarez 2159","precio":420000000,"moneda":"ARS","m2_total":210,"dormitorios":3,"banos":2,"desc":"Propiedad única con sólida estructura en piedra y belleza atemporal.","inm":"Sosa Inmobiliaria","email":"","tel":"+5493764422483","url":"https://www.sosainmobiliaria.com","fotos":[],"nuevo":False},
  {"tipo":"Terreno","titulo":"Lote parquizado 650m² — Santa Inés, Garupá","barrio":"Garupá","direccion":"Bº Don Fernando, Garupá","precio":27000000,"moneda":"ARS","m2_total":650,"dormitorios":0,"banos":0,"desc":"Lote parquizado en zona tranquila residencial, a 15 minutos de Posadas.","inm":"Sosa Inmobiliaria","email":"","tel":"+5493764422483","url":"https://www.sosainmobiliaria.com","fotos":["https://cdn-images.xintelweb.com/upload/sos5090_2.jpg"],"nuevo":True},
  # ZAPANI
  {"tipo":"Departamento","titulo":"Dpto 1 dorm. con placard — Estado de Israel","barrio":"Posadas","direccion":"Estado de Israel 4355 c/ Av. Maipú","precio":58000,"moneda":"USD","m2_total":42,"dormitorios":1,"banos":1,"desc":"Departamento luminoso, ideal primera vivienda o inversión.","inm":"Zapani Inmobiliaria","email":"inmobiliariazapani@hotmail.com","tel":"+5493764436113","url":"https://www.inmobiliariazapani.com.ar","fotos":[],"nuevo":True},
  {"tipo":"Terreno","titulo":"Terreno 400m² con financiación — Posadas","barrio":"Posadas","direccion":"Brig. Pedernera, zona residencial","precio":26000,"moneda":"USD","m2_total":400,"dormitorios":0,"banos":0,"desc":"Lote con el mejor plan de financiación del mercado.","inm":"Zapani Inmobiliaria","email":"inmobiliariazapani@hotmail.com","tel":"+5493764436113","url":"https://www.inmobiliariazapani.com.ar","fotos":[],"nuevo":False},
  {"tipo":"Casa","titulo":"Casa 3 dorms. con amplio terreno — zona Maipú","barrio":"Posadas","direccion":"Av. Maipú al 4300","precio":98000,"moneda":"USD","m2_total":220,"dormitorios":3,"banos":2,"desc":"Casa familiar con terreno, garage, ideal para ampliar o construir.","inm":"Zapani Inmobiliaria","email":"inmobiliariazapani@hotmail.com","tel":"+5493764436113","url":"https://www.inmobiliariazapani.com.ar","fotos":[],"nuevo":True},
]

def semilla_a_props():
    props = []
    for i,s in enumerate(SEMILLA_RAW):
        lat,lng = geocode_fallback(s["barrio"]+" "+s.get("direccion",""))
        props.append(mk_prop(
            id=pid("semilla",f"{i}{s['titulo'][:20]}"),
            ref=f"SEM-{i+1:03d}",
            fuente=s.get("inm","Local"),
            tipo=s["tipo"], titulo=s["titulo"],
            barrio=s["barrio"], direccion=s.get("direccion",""),
            precio=s["precio"], moneda=s["moneda"],
            m2=s.get("m2_total"), dorms=s.get("dormitorios"),
            banos=s.get("banos"),
            desc=s.get("desc",""), fotos=s.get("fotos",[]),
            url=s.get("url",""), inm=s.get("inm",""),
            email=s.get("email",""), tel=s.get("tel",""),
            lat=lat, lng=lng, nuevo=s.get("nuevo",False),
        ))
    return props

# ── MOTOR PRINCIPAL ────────────────────────────────────────────────────────
async def ejecutar_scraping():
    log.info("=== Iniciando scraping v3 ===")
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
            scrape_torres(client),
            scrape_davina(client),
            scrape_sosa(client),
            scrape_zapani(client),
            return_exceptions=True,
        )
    scraped = []
    for t in tareas:
        if isinstance(t, list): scraped.extend(t)
        elif isinstance(t, Exception): log.error(f"Tarea fallida: {t}")
    if len(scraped) < 5:
        log.warning("Scraping sin resultados — usando semilla")
        scraped = semilla_a_props()
    else:
        scraped = dedup(scraped + semilla_a_props())
    scraped = [p for p in scraped if p["precio"] > 0]
    scraped.sort(key=lambda x: x["precio"])
    log.info(f"=== Total: {len(scraped)} propiedades ===")
    return scraped

# ── PERSISTENCIA ───────────────────────────────────────────────────────────
def guardar(props):
    global _MEM, _FECHA
    _MEM = props; _FECHA = datetime.now().isoformat()
    try: DATA_FILE.write_text(json.dumps(props, ensure_ascii=False, indent=2))
    except Exception as e: log.warning(f"No se pudo guardar en disco: {e}")

def cargar():
    global _MEM
    if _MEM: return _MEM
    if DATA_FILE.exists():
        try: _MEM = json.loads(DATA_FILE.read_text()); return _MEM
        except: pass
    return []

def necesita_refresh():
    if _MEM and _FECHA:
        return (datetime.now()-datetime.fromisoformat(_FECHA)) > timedelta(hours=REFRESH_HOURS)
    return True

# ── FASTAPI ────────────────────────────────────────────────────────────────
app = FastAPI(title="InmoPosadas API", version="3.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    global _SCRAPING
    if not cargar():
        guardar(semilla_a_props())
        log.info("Semilla cargada")
    if not _SCRAPING:
        _SCRAPING = True
        asyncio.create_task(_tarea())

async def _tarea():
    global _SCRAPING
    try: props = await ejecutar_scraping(); guardar(props)
    finally: _SCRAPING = False

@app.get("/health")
async def health():
    return {"status":"ok","propiedades":len(_MEM),"ts":datetime.now().isoformat()}

@app.get("/api/status")
async def api_status():
    return {"scraping_en_curso":_SCRAPING,"ultima_actualizacion":_FECHA or None,
            "total_propiedades":len(_MEM),"datos_disponibles":bool(_MEM)}

@app.post("/api/refresh")
async def refresh(bg: BackgroundTasks):
    global _SCRAPING
    if _SCRAPING: return {"message":"Scraping en curso, esperá."}
    _SCRAPING = True; bg.add_task(_tarea)
    return {"message":"Scraping iniciado (2-5 min)."}

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
    return {"total":len(ps),
            "precio_usd_min":min(usd) if usd else 0,
            "precio_usd_max":max(usd) if usd else 0,
            "precio_usd_promedio":int(sum(usd)/len(usd)) if usd else 0,
            "por_tipo":tipos,"por_fuente":fuentes,
            "top_barrios":dict(sorted(barrios.items(),key=lambda x:-x[1])[:10])}

FRONTEND = BASE_DIR / "frontend"
if FRONTEND.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="static")
