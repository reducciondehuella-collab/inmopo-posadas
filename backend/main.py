"""
InmoPosadas — Backend FastAPI v4
- Proxy de imágenes (resuelve hotlink protection)
- 10+ inmobiliarias de Posadas
- Coordenadas precisas por dirección
"""
import asyncio, json, logging, os, re, hashlib, random, base64
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
from fastapi import FastAPI, Query, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

BASE_DIR  = Path(__file__).parent.parent
DATA_DIR  = BASE_DIR / "data"
DATA_FILE = DATA_DIR / "propiedades.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)

REFRESH_HOURS = 6
MAX_PAGES     = 15
DELAY         = 0.8
GEO_DELAY     = 1.1

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("inmopo")

_MEM:      list[dict] = []
_FECHA:    str        = ""
_SCRAPING: bool       = False

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9",
           "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}

# ── BARRIOS POSADAS ────────────────────────────────────────────────────────
BARRIOS = {
    "villa sarita":(-27.3540,-55.8910),"villa urquiza":(-27.3700,-55.8800),
    "villa cabello":(-27.3550,-55.8850),"villa blosset":(-27.3600,-55.9150),
    "barrio norte":(-27.3580,-55.9000),"jardín américa":(-27.3740,-55.9100),
    "jardin america":(-27.3740,-55.9100),"itaembé guazú":(-27.3900,-55.8800),
    "itaembe guazu":(-27.3900,-55.8800),"itaembé miní":(-27.3550,-55.9100),
    "itaembe mini":(-27.3550,-55.9100),"miguel lanús":(-27.3680,-55.9050),
    "miguel lanus":(-27.3680,-55.9050),"costanera":(-27.3720,-55.8950),
    "centro":(-27.3671,-55.8964),"peñaflor":(-27.3650,-55.9200),
    "zaimán":(-27.3820,-55.9150),"zaiman":(-27.3820,-55.9150),
    "san jorge":(-27.3830,-55.9200),"nueva esperanza":(-27.3900,-55.9300),
    "el yerbal":(-27.3900,-55.8800),"la eugenia":(-27.4000,-55.9000),
    "palomar":(-27.3760,-55.9000),"aguacate":(-27.3900,-55.8950),
    "garupá":(-27.4100,-55.8900),"garupa":(-27.4100,-55.8900),
    "panambi":(-27.3480,-55.9200),"kennedy":(-27.3780,-55.9050),
    "km 4":(-27.3850,-55.9050),"km 8":(-27.3950,-55.9200),
    "ruta 12":(-27.3620,-55.9300),"santa catalina":(-27.3750,-55.9100),
    "el palmar":(-27.3700,-55.9300),"posadas":(-27.3671,-55.8964),
}
_GEO_CACHE: dict = {}

def geo_fallback(t:str)->tuple:
    t=t.lower()
    for b,c in sorted(BARRIOS.items(),key=lambda x:-len(x[0])):
        if b in t:
            return (round(c[0]+random.uniform(-.0025,.0025),6),
                    round(c[1]+random.uniform(-.0025,.0025),6))
    return (round(-27.3671+random.uniform(-.02,.02),6),
            round(-55.8964+random.uniform(-.02,.02),6))

async def geo_nominatim(client,dir_:str)->tuple|None:
    k=dir_.lower().strip()
    if k in _GEO_CACHE: return _GEO_CACHE[k]
    try:
        r=await client.get("https://nominatim.openstreetmap.org/search",
            params={"q":f"{dir_}, Posadas, Misiones, Argentina",
                    "format":"json","limit":1,"countrycodes":"ar"},
            headers={**HEADERS,"User-Agent":"InmoPosadas/1.0 (inmopo@posadas.ar)"},
            timeout=8)
        if r.status_code==200:
            d=r.json()
            if d:
                lat,lng=round(float(d[0]["lat"]),6),round(float(d[0]["lon"]),6)
                if -27.55<lat<-27.20 and -56.10<lng<-55.65:
                    _GEO_CACHE[k]=(lat,lng)
                    await asyncio.sleep(GEO_DELAY)
                    return (lat,lng)
    except: pass
    return None

# ── HELPERS ────────────────────────────────────────────────────────────────
def pid(f,r): return hashlib.md5(f"{f}:{r}".encode()).hexdigest()[:12]
def num(v):
    try: return int(float(str(v or "").replace(",",".")))
    except: return None
def strip(s): return re.sub(r'<[^>]+>','',str(s)).strip()
def tipo_de(t):
    t=t.lower()
    if any(x in t for x in["duplex","dúplex"]): return "Duplex"
    if re.search(r'\bph\b|penthouse',t): return "PH"
    if any(x in t for x in["departamento","depto","dpto","apartamento"]): return "Departamento"
    if any(x in t for x in["terreno","lote","fracción","fraccion","campo","chacra"]): return "Terreno"
    if any(x in t for x in["local","oficina","galpon","galpón","depósito","deposito","galería"]): return "Local"
    return "Casa"
def barrio_de(t):
    t2=t.lower()
    for b in sorted(BARRIOS.keys(),key=len,reverse=True):
        if b in t2 and b!="posadas": return b.title()
    return "Posadas"
def og_img(html:str)->list[str]:
    m=re.findall(r'meta-og:image:\s*(https?://[^\s\n]+)',html)
    if not m: m=re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',html,re.I)
    return [i for i in m if not re.search(r'logo|icon|favicon',i,re.I)][:1]
def gmaps_coords(html:str)->tuple|None:
    m=re.search(r'maps\.google\.com[^"\']*[?&]q=([\-\d\.]+),([\-\d\.]+)',html)
    return (float(m.group(1)),float(m.group(2))) if m else None

def mk(**kw)->dict:
    return {"id":kw.get("id",""),"ref_externa":kw.get("ref",""),
            "fuente":kw.get("fuente",""),"tipo":kw.get("tipo","Casa"),
            "titulo":kw.get("titulo","")[:120],"barrio":kw.get("barrio","Posadas"),
            "direccion":kw.get("dir","")[:120],"ciudad":"Posadas",
            "precio":kw.get("precio",0),"moneda":kw.get("moneda","USD"),
            "m2_total":kw.get("m2"),"m2_cubierto":kw.get("m2c"),
            "dormitorios":kw.get("dorms"),"banos":kw.get("banos"),
            "descripcion":kw.get("desc","")[:400],
            "fotos":kw.get("fotos",[]),
            "url":kw.get("url",""),"inmobiliaria":kw.get("inm",""),
            "email_inm":kw.get("email",""),"tel_inm":kw.get("tel",""),
            "lat":kw.get("lat",0.0),"lng":kw.get("lng",0.0),
            "fecha":kw.get("fecha",datetime.today().strftime("%Y-%m-%d")),
            "nuevo":kw.get("nuevo",False)}

def proxy_url(img_url:str, referer:str)->str:
    """Convierte una URL de imagen en una URL del proxy interno."""
    if not img_url: return ""
    encoded = base64.urlsafe_b64encode(img_url.encode()).decode()
    ref_enc = base64.urlsafe_b64encode(referer.encode()).decode()
    return f"/img/{encoded}/{ref_enc}"

def proxificar(fotos:list, referer:str)->list:
    """Convierte todas las URLs de fotos al proxy interno."""
    return [proxy_url(f, referer) for f in fotos if f]

# ── PROXY DE IMÁGENES ──────────────────────────────────────────────────────
# Se agrega DESPUÉS de definir la app, ver abajo

# ── MERCADOLIBRE ───────────────────────────────────────────────────────────
async def scrape_ml(client):
    res=[]
    CATS={"MLA1468":"Casa","MLA1471":"Departamento","MLA1473":"Terreno",
          "MLA1476":"Local","MLA1472":"PH","MLA150726":"Duplex"}
    for cat,tipo in CATS.items():
        offset=0
        for _ in range(MAX_PAGES):
            url=(f"https://api.mercadolibre.com/sites/MLA/search"
                 f"?category={cat}&state_id=AR-N&city=Posadas&offset={offset}&limit=48")
            try:
                r=await client.get(url,headers={**HEADERS,"Accept":"application/json"},timeout=20)
                if r.status_code!=200: break
                data=r.json()
            except: break
            items=data.get("results",[])
            total=data.get("paging",{}).get("total",0)
            if not items: break
            for it in items:
                loc=it.get("location",{})
                ciudad=loc.get("city",{}).get("name","")
                if ciudad and "posadas" not in ciudad.lower(): continue
                barrio=(loc.get("neighborhood",{}).get("name","") or
                        it.get("seller_address",{}).get("city",{}).get("name","") or "Posadas")
                lat=loc.get("latitude") or loc.get("lat")
                lng=loc.get("longitude") or loc.get("lon")
                if not lat or not lng: lat,lng=geo_fallback(barrio)
                attrs={a["id"]:a.get("value_name") for a in it.get("attributes",[])}
                # MercadoLibre sirve sus fotos sin restricción de hotlink
                fotos=[p.get("url","").replace("-I.jpg","-O.jpg")
                       for p in it.get("pictures",[])[:8] if p.get("url")]
                if it.get("thumbnail") and not fotos:
                    fotos=[it["thumbnail"].replace("-I.jpg","-O.jpg")]
                res.append(mk(
                    id=pid("ml",it["id"]),ref=it["id"],fuente="MercadoLibre",
                    tipo=tipo,titulo=it.get("title",""),barrio=barrio,
                    dir=it.get("seller_address",{}).get("address_line",barrio),
                    precio=it.get("price",0),moneda=it.get("currency_id","USD"),
                    m2=num(attrs.get("TOTAL_AREA") or attrs.get("SURFACE_TOTAL")),
                    m2c=num(attrs.get("COVERED_AREA") or attrs.get("SURFACE_COVERED")),
                    dorms=num(attrs.get("BEDROOMS") or attrs.get("ROOMS")),
                    banos=num(attrs.get("BATHROOMS")),
                    desc=it.get("title",""),fotos=fotos,url=it.get("permalink",""),
                    inm=it.get("seller",{}).get("nickname","ML").title().replace("_"," "),
                    lat=round(float(lat),6),lng=round(float(lng),6),
                    fecha=it.get("date_created","")[:10],
                    nuevo="estrenar" in it.get("title","").lower(),
                ))
            offset+=48
            if offset>=min(total,MAX_PAGES*48): break
            await asyncio.sleep(DELAY)
    log.info(f"MercadoLibre: {len(res)}")
    return res

# ── PARSER XINTEL (Sosa, Fogeler, Innova, etc.) ────────────────────────────
def parse_xintel_listado(html:str, inm:str, base_url:str, email:str, tel:str)->list:
    """
    Parsea el listado de cualquier inmobiliaria que use Xintel/Amaira.
    Extrae fotos directamente de las tarjetas (cdn-images.xintelweb.com).
    """
    res=[]
    # Cada tarjeta tiene: imagen cdn, link al slug, titulo, ubicacion, precio
    tarjetas=re.findall(
        r'!\[([^\]]*)\]\((https://cdn-images\.xintelweb\.com/[^\)]+)\)\s*\n'
        r'\s*\[([a-z0-9\-]+)\]\(https://[^\)]+\)\s*\n'
        r'\s*###\s*\[([^\]]+)\]\([^\)]+\)\s*\n'
        r'\s*([^\n]+)\s*\n'
        r'(?:[^\n]*\n){0,3}'
        r'\s*\*\*[A-Z]{3}\d+\*\*\s*(U\$S|U\$|USD|\$)\s*([\d\.,]+)',
        html, re.MULTILINE
    )
    for alt, foto_url, slug, titulo, ubicacion, mon_sym, precio_str in tarjetas:
        try:
            precio=int(precio_str.replace(".","").replace(",",""))
            if precio<=0: continue
            moneda="USD" if "U" in mon_sym.upper() else "ARS"
            barrio=barrio_de(ubicacion+" "+titulo)
            lat,lng=geo_fallback(barrio)
            # foto ya disponible desde el listado
            fotos=proxificar([foto_url], base_url)
            res.append(mk(
                id=pid(inm,slug),ref=slug,fuente=inm,tipo=tipo_de(titulo),
                titulo=titulo.strip(),barrio=barrio,
                dir=ubicacion.strip(),precio=precio,moneda=moneda,
                desc=titulo,fotos=fotos,
                url=f"https://{base_url.split('/')[2]}/{slug}",
                inm=inm,email=email,tel=tel,lat=lat,lng=lng,
            ))
        except: continue
    return res

async def scrape_xintel(client, inm:str, base_url:str, email:str, tel:str, max_pag:int=8)->list:
    """Scraper genérico para cualquier sitio Xintel."""
    res=[]
    for pag in range(0, max_pag):
        url=f"{base_url}&p={pag}" if "?" in base_url else f"{base_url}?ope=V&p={pag}"
        try:
            r=await client.get(url,headers=HEADERS,timeout=25,follow_redirects=True)
            if r.status_code!=200: break
            html=r.text
        except Exception as e:
            log.warning(f"{inm} p{pag}: {e}"); break
        items=parse_xintel_listado(html,inm,url,email,tel)
        if not items: break
        res.extend(items)
        log.info(f"{inm} p{pag}: {len(items)}")
        await asyncio.sleep(DELAY)
    return res

# ── ZONAPROP ───────────────────────────────────────────────────────────────
async def scrape_zonaprop(client):
    res=[]
    for pag in range(1,MAX_PAGES+1):
        url=("https://www.zonaprop.com.ar/inmuebles-venta-posadas.html" if pag==1
             else f"https://www.zonaprop.com.ar/inmuebles-venta-posadas-pagina-{pag}.html")
        try:
            r=await client.get(url,headers=HEADERS,timeout=25,follow_redirects=True)
            if r.status_code!=200: break; html=r.text
            html=r.text
        except Exception as e: log.warning(f"ZonaProp p{pag}: {e}"); break
        arts=re.findall(r'<article[^>]+data-id=["\']?(\d+)["\']?[^>]*>(.*?)</article>',
                        html,re.DOTALL|re.IGNORECASE)
        if not arts: break
        for aid,ahtml in arts:
            try:
                tm=re.search(r'<h2[^>]*>(.*?)</h2>',ahtml,re.DOTALL)
                titulo=strip(tm.group(1)) if tm else f"ZP-{aid}"
                pm=re.search(r'(USD?|US\$|\$)\s*([\d\.,]+)',ahtml)
                precio,moneda=0,"USD"
                if pm:
                    moneda="USD" if "U" in pm.group(1).upper() else "ARS"
                    precio=int(pm.group(2).replace(".","").replace(",",""))
                dm=re.search(r'class=["\'][^"\']*address[^"\']*["\'][^>]*>(.*?)</',ahtml,re.DOTALL)
                dir_=strip(dm.group(1)) if dm else "Posadas"
                sm=re.search(r'(\d+)\s*m[²2]',ahtml)
                drm=re.search(r'(\d+)\s*(?:dorm|amb)',ahtml,re.IGNORECASE)
                um=re.search(r'href=["\'](/propiedades/[^"\']+)["\']',ahtml)
                url_p="https://www.zonaprop.com.ar"+um.group(1) if um else ""
                foto_m=re.findall(r'(?:data-flickity-lazyload|data-src)=["\']'
                                  r'(https://[^"\']+\.(?:jpg|jpeg|webp)[^"\']*)["\']',ahtml,re.I)
                fotos=proxificar(list(dict.fromkeys(foto_m))[:4],"https://www.zonaprop.com.ar")
                barrio=barrio_de(dir_+" "+titulo)
                lat,lng=geo_fallback(barrio)
                if precio<=0: continue
                res.append(mk(id=pid("zp",aid),ref=f"ZP-{aid}",fuente="ZonaProp",
                    tipo=tipo_de(titulo+" "+dir_),titulo=titulo,barrio=barrio,dir=dir_,
                    precio=precio,moneda=moneda,m2=num(sm.group(1)) if sm else None,
                    dorms=num(drm.group(1)) if drm else None,
                    desc=titulo,fotos=fotos,url=url_p,inm="ZonaProp",lat=lat,lng=lng))
            except: continue
        await asyncio.sleep(DELAY)
    log.info(f"ZonaProp: {len(res)}")
    return res

# ── ARGENPROP ──────────────────────────────────────────────────────────────
async def scrape_argenprop(client):
    res=[]
    for pag in range(1,MAX_PAGES+1):
        url=f"https://www.argenprop.com/inmuebles-en-venta-en-posadas?pagina={pag}"
        try:
            r=await client.get(url,headers=HEADERS,timeout=25,follow_redirects=True)
            if r.status_code!=200: break; html=r.text
            html=r.text
        except Exception as e: log.warning(f"Argenprop p{pag}: {e}"); break
        cards=re.findall(r'<div[^>]+listing__item[^>]*>(.*?)</div>\s*</div>',
                         html,re.DOTALL|re.IGNORECASE)
        if not cards: break
        for j,card in enumerate(cards):
            try:
                tm=re.search(r'listing__title[^"\']*["\'][^>]*>(.*?)</',card,re.DOTALL)
                titulo=strip(tm.group(1)) if tm else f"AP-{j}"
                pm=re.search(r'(USD?|US\$|\$)\s*([\d\.,]+)',card)
                precio,moneda=0,"USD"
                if pm:
                    moneda="USD" if "U" in pm.group(1).upper() else "ARS"
                    precio=int(pm.group(2).replace(".","").replace(",",""))
                dm=re.search(r'listing__location[^"\']*["\'][^>]*>(.*?)</',card,re.DOTALL)
                dir_=strip(dm.group(1)) if dm else "Posadas"
                sm=re.search(r'(\d+)\s*m[²2]',card)
                drm=re.search(r'(\d+)\s*dorm',card,re.IGNORECASE)
                um=re.search(r'href=["\']([^"\']+propiedad[^"\']+)["\']',card)
                url_p="https://www.argenprop.com"+um.group(1) if um else ""
                foto_m=re.findall(r'(?:data-src|data-lazy|src)=["\']'
                                  r'(https://[^"\']+\.(?:jpg|jpeg|webp)[^"\']*)["\']',card,re.I)
                fotos=proxificar(list(dict.fromkeys(foto_m))[:4],"https://www.argenprop.com")
                barrio=barrio_de(dir_+" "+titulo)
                lat,lng=geo_fallback(barrio)
                if precio<=0: continue
                res.append(mk(id=pid("ap",f"{j}{titulo[:15]}"),ref=f"AP-{j}",
                    fuente="Argenprop",tipo=tipo_de(titulo+" "+dir_),titulo=titulo,
                    barrio=barrio,dir=dir_,precio=precio,moneda=moneda,
                    m2=num(sm.group(1)) if sm else None,
                    dorms=num(drm.group(1)) if drm else None,
                    desc=titulo,fotos=fotos,url=url_p,inm="Argenprop",lat=lat,lng=lng))
            except: continue
        await asyncio.sleep(DELAY)
    log.info(f"Argenprop: {len(res)}")
    return res

# ── TORRES INMOBILIARIA ────────────────────────────────────────────────────
async def scrape_torres(client):
    res=[]
    ids=set()
    for pag in range(1,MAX_PAGES+1):
        url=(f"https://inmobiliariatorres.com.ar/2023/properties-grid-3/index.php"
             f"?tipo_operacion=venta&pagina={pag}")
        try:
            r=await client.get(url,headers=HEADERS,timeout=25,follow_redirects=True)
            if r.status_code!=200: break
            html=r.text
        except: break
        nuevos=set(re.findall(r'ficha\.php\?id=(\d+)',html))
        if not nuevos or nuevos.issubset(ids): break
        ids.update(nuevos)
        await asyncio.sleep(DELAY)
    log.info(f"Torres IDs: {len(ids)}")
    for fid in list(ids)[:100]:
        url_f=f"https://inmobiliariatorres.com.ar/2023/ficha.php?id={fid}"
        try:
            r=await client.get(url_f,headers=HEADERS,timeout=20,follow_redirects=True)
            if r.status_code!=200: continue
            html=r.text
        except: continue
        try:
            tm=re.search(r'<h1[^>]*>(.*?)</h1>',html,re.DOTALL)
            titulo=strip(tm.group(1)) if tm else f"Torres-{fid}"
            um_usd=re.search(r'USD\s*([\d\.,]+)',html,re.I)
            pm=re.search(r'\$([\d\.,]+)',html)
            if um_usd: precio=int(um_usd.group(1).replace(".","").replace(",","")); moneda="USD"
            elif pm: precio=int(pm.group(1).replace(".","").replace(",","")); moneda="ARS"
            else: continue
            tipo_m=re.search(r'\[(Casas|Departamentos|Terrenos|Locales|Oficinas|Duplex)\]',html)
            tmap={"Casas":"Casa","Departamentos":"Departamento","Terrenos":"Terreno",
                  "Locales":"Local","Oficinas":"Local","Duplex":"Duplex"}
            tipo=tmap.get(tipo_m.group(1),"Casa") if tipo_m else tipo_de(titulo)
            dm=re.search(r'Dormitorios:\s*(\d+)',html)
            bm=re.search(r'Baños:\s*(\d+)',html)
            sm=re.search(r'Superficie\s*:\s*([\d.]+)\s*m2',html)
            coords=gmaps_coords(html)
            if coords: lat,lng=coords[0],coords[1]
            else: lat,lng=geo_fallback(titulo)
            dir_m=re.search(r'\[([^\]]{5,60})\]\(https://maps\.google',html)
            dir_=dir_m.group(1).strip() if dir_m else "Posadas"
            barrio=barrio_de(dir_+" "+titulo)
            # Fotos con proxy (Torres bloquea hotlink)
            foto_ids=re.findall(r'/sistema/fotos/(\d+)\.jpg',html)
            fotos_raw=[f"https://inmobiliariatorres.com.ar/sistema/fotos/{f}.jpg" for f in foto_ids]
            if not fotos_raw: fotos_raw=og_img(html)
            fotos=proxificar(fotos_raw[:6],"https://inmobiliariatorres.com.ar")
            desc_m=re.search(r'### Descripción\s*\n+(.*?)(?=###|\Z)',html,re.DOTALL)
            desc=strip(desc_m.group(1))[:400] if desc_m else titulo
            if precio<=0: continue
            res.append(mk(id=pid("torres",fid),ref=f"TORRES-{fid}",
                fuente="Torres Inmobiliaria",tipo=tipo,titulo=titulo.title(),
                barrio=barrio,dir=dir_,precio=precio,moneda=moneda,
                m2=int(float(sm.group(1))) if sm else None,
                dorms=int(dm.group(1)) if dm else None,
                banos=int(bm.group(1)) if bm else None,
                desc=desc,fotos=fotos,url=url_f,
                inm="Torres Inmobiliaria",
                email="info@inmobiliariatorres.com.ar",tel="+543764533092",
                lat=round(lat,6),lng=round(lng,6)))
        except: continue
        await asyncio.sleep(DELAY)
    log.info(f"Torres Inmobiliaria: {len(res)}")
    return res

# ── DAVIÑA INMOBILIARIA ────────────────────────────────────────────────────
async def scrape_davina(client):
    res=[]
    slugs=[]
    for pag in range(1,9):
        url="https://davinia.com.ar/" if pag==1 else f"https://davinia.com.ar/?paged-1={pag}"
        try:
            r=await client.get(url,headers=HEADERS,timeout=25,follow_redirects=True)
            if r.status_code!=200: break; html=r.text
            html=r.text
        except Exception as e: log.warning(f"Daviña p{pag}: {e}"); break
        nuevos=list(dict.fromkeys(re.findall(r'https://davinia\.com\.ar/property/([a-z0-9\-]+)/',html)))
        if not nuevos: break
        slugs.extend([s for s in nuevos if s not in slugs])
        await asyncio.sleep(DELAY)
    log.info(f"Daviña slugs: {len(slugs)}")
    for slug in slugs[:100]:
        url_f=f"https://davinia.com.ar/property/{slug}/"
        try:
            r=await client.get(url_f,headers=HEADERS,timeout=20,follow_redirects=True)
            if r.status_code!=200: continue
            html=r.text
        except: continue
        try:
            tm=re.search(r'# ([^\n]+)\n',html)
            titulo=tm.group(1).strip() if tm else slug.replace("-"," ").title()
            pm_usd=re.search(r'USD\s*([\d,\.]+)',html)
            pm_ars=re.search(r'\$\s*([\d\.]+)',html)
            if pm_usd: precio=int(pm_usd.group(1).replace(",","").replace(".","").replace(" ","")); moneda="USD"
            elif pm_ars: precio=int(pm_ars.group(1).replace(".","").replace(" ","")); moneda="ARS"
            else: continue
            if precio<=0: continue
            dir_m=re.search(r'([^\n]{10,80}(?:Posadas|Misiones|N330\d)[^\n]*)',html)
            dir_=dir_m.group(1).strip() if dir_m else "Posadas, Misiones"
            coords=gmaps_coords(html)
            if not coords: coords=await geo_nominatim(client,dir_)
            lat,lng=coords if coords else geo_fallback(barrio_de(dir_+" "+titulo))
            barrio=barrio_de(dir_+" "+titulo)
            dm=re.search(r'\*\*(\d+)\*\*\s*camas',html)
            bm=re.search(r'\*\*(\d+)\*\*\s*ba(?:ño|lneario)',html,re.I)
            sm=re.search(r'\*\*([\d.]+)\*\*\s*m²',html)
            # Fotos originales WP (sin -NxN en el nombre)
            fotos_raw=list(dict.fromkeys([
                f for f in re.findall(
                    r'https://davinia\.com\.ar/wp-content/uploads/[\d/]+[^"\')\s]+\.(?:jpg|jpeg|webp|png)',html)
                if not re.search(r'-\d+x\d+\.',f)
            ]))[:8]
            if not fotos_raw: fotos_raw=og_img(html)
            fotos=proxificar(fotos_raw,"https://davinia.com.ar")
            desc_m=re.search(r'### Descripción\s*\n+(.*?)(?=###|\Z)',html,re.DOTALL)
            desc=strip(desc_m.group(1))[:400] if desc_m else titulo
            res.append(mk(id=pid("davina",slug),ref=slug,
                fuente="Daviña Inmobiliaria",tipo=tipo_de(titulo),titulo=titulo,
                barrio=barrio,dir=dir_,precio=precio,moneda=moneda,
                m2=int(float(sm.group(1))) if sm else None,
                dorms=int(dm.group(1)) if dm else None,
                banos=int(bm.group(1)) if bm else None,
                desc=desc,fotos=fotos,url=url_f,
                inm="Daviña Inmobiliaria",
                email="davinainmobiliaria@gmail.com",tel="+5493764425983",
                lat=round(lat,6),lng=round(lng,6)))
        except: continue
        await asyncio.sleep(DELAY)
    log.info(f"Daviña Inmobiliaria: {len(res)}")
    return res

# ── SINGULAR INMOBILIARIA ──────────────────────────────────────────────────
async def scrape_singular(client):
    res=[]
    for pag in range(1, MAX_PAGES+1):
        url=(f"https://www.inmobiliariamasingenieria.com.ar/propiedades.php"
             f"?z=Posadas%2C+Misiones&c=&o=1&p={pag}")
        try:
            r=await client.get(url,headers=HEADERS,timeout=25,follow_redirects=True)
            if r.status_code!=200: break
            html=r.text
        except Exception as e: log.warning(f"Singular p{pag}: {e}"); break
        # Props: /propiedades/slug o /NNN
        slugs=re.findall(r'href=["\']/((?:propiedades/|)[a-z0-9\-]+-\d+)["\']',html)
        if not slugs: break
        for slug in slugs:
            try:
                url_f=f"https://www.inmobiliariamasingenieria.com.ar/{slug}"
                r2=await client.get(url_f,headers=HEADERS,timeout=20,follow_redirects=True)
                if r2.status_code!=200: continue
                h=r2.text
                tm=re.search(r'<h1[^>]*>(.*?)</h1>',h,re.DOTALL)
                titulo=strip(tm.group(1)) if tm else slug
                pm_usd=re.search(r'USD\s*([\d\.,]+)',h,re.I)
                pm_ars=re.search(r'\$\s*([\d\.]+)',h)
                if pm_usd: precio=int(pm_usd.group(1).replace(".","").replace(",","")); moneda="USD"
                elif pm_ars: precio=int(pm_ars.group(1).replace(".","").replace(",","")); moneda="ARS"
                else: continue
                if precio<=0: continue
                dir_m=re.search(r'(?:Dirección|Ubicación|direcci[oó]n)[:\s]+([^\n<]{5,80})',h,re.I)
                dir_=dir_m.group(1).strip() if dir_m else "Posadas, Misiones"
                barrio=barrio_de(dir_+" "+titulo)
                coords=await geo_nominatim(client,dir_)
                lat,lng=coords if coords else geo_fallback(barrio)
                foto_m=re.findall(r'src=["\']([^"\']+/admin/propiedades/[^"\']+\.(?:jpg|jpeg|png|webp))["\']',h,re.I)
                fotos=proxificar(list(dict.fromkeys(foto_m))[:6],"https://www.inmobiliariamasingenieria.com.ar")
                dm=re.search(r'(\d+)\s*[Dd]ormitorio',h)
                sm=re.search(r'(\d+)\s*m[²2]',h)
                res.append(mk(id=pid("singular",slug),ref=slug,
                    fuente="Singular Inmobiliaria",tipo=tipo_de(titulo),titulo=titulo,
                    barrio=barrio,dir=dir_,precio=precio,moneda=moneda,
                    m2=num(sm.group(1)) if sm else None,
                    dorms=num(dm.group(1)) if dm else None,
                    desc=titulo,fotos=fotos,url=url_f,
                    inm="Singular Inmobiliaria",
                    email="singular@inmobiliariamasingenieria.com.ar",tel="+5493764724379",
                    lat=round(lat,6),lng=round(lng,6)))
                await asyncio.sleep(DELAY)
            except: continue
        if len(res)>80: break
        await asyncio.sleep(DELAY)
    log.info(f"Singular Inmobiliaria: {len(res)}")
    return res

# ── SCRAPING PRINCIPAL ─────────────────────────────────────────────────────
async def ejecutar_scraping():
    log.info("=== Scraping v4 iniciado ===")
    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=6),
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    ) as client:
        tareas=await asyncio.gather(
            scrape_ml(client),
            scrape_zonaprop(client),
            scrape_argenprop(client),
            scrape_torres(client),
            scrape_davina(client),
            scrape_singular(client),
            # Xintel: Sosa, Fogeler, Innova
            scrape_xintel(client,"Sosa Inmobiliaria",
                "https://www.sosainmobiliaria.com/propiedades.php?ope=V",
                "","+ 5493764422483",10),
            scrape_xintel(client,"Mónica Fogeler",
                "https://www.monicafogeler.com.ar/propiedades?ope=V&b=All&tipo=All&loc=All&ambi_1=All",
                "monica.fogeler@gmail.com","+5493764129799",6),
            scrape_xintel(client,"Innova Inmobiliaria",
                "https://innovaservicioinmobiliario.com/propiedades?ope=V",
                "innovaserv.inmobiliarios@gmail.com","+5493764150099",5),
            return_exceptions=True,
        )
    scraped=[]
    for t in tareas:
        if isinstance(t,list): scraped.extend(t)
        elif isinstance(t,Exception): log.error(f"Tarea fallida: {t}")
    if len(scraped)<10:
        log.warning("Scraping insuficiente — usando semilla")
        scraped=semilla_props()
    else:
        scraped=dedup(scraped+semilla_props())
    scraped=[p for p in scraped if p["precio"]>0]
    scraped.sort(key=lambda x:x["precio"])
    log.info(f"=== Total: {len(scraped)} propiedades ===")
    return scraped

def dedup(props):
    seen_id,seen_k=set(),set()
    out=[]
    for p in props:
        if p["id"] in seen_id: continue
        k=f"{p['precio']}-{p['tipo'][:3]}-{p['barrio'][:8]}"
        if k in seen_k and p["precio"]>0: continue
        seen_id.add(p["id"]); seen_k.add(k)
        out.append(p)
    return out

# ── SEMILLA ────────────────────────────────────────────────────────────────
def semilla_props():
    SEED=[
      # MercadoLibre / portales
      {"f":"MercadoLibre","t":"Departamento","tit":"Dpto 2 dorms frente al río — Costanera","b":"Costanera","d":"Av. Costanera 850","p":68000,"m":"USD","m2":52,"dorms":1,"banos":1,"desc":"Luminoso dpto frente al Paraná, balcón panorámico.","fotos":[],"url":"https://www.mercadolibre.com.ar","inm":"ML Posadas","email":"","tel":""},
      {"f":"ZonaProp","t":"Terreno","tit":"Lote premium 600m² — Barrio Norte","b":"Barrio Norte","d":"Barrio Los Aromos Lote 12","p":48000,"m":"USD","m2":600,"dorms":0,"banos":0,"desc":"Lote en barrio privado, todos los servicios.","fotos":[],"url":"https://www.zonaprop.com.ar","inm":"ZonaProp","email":"","tel":""},
      # Torres
      {"f":"Torres Inmobiliaria","t":"Departamento","tit":"Edificio Victoria Regia — 2 dorms","b":"Centro","d":"Alvear N° 1977","p":171130,"m":"USD","m2":85,"dorms":2,"banos":2,"desc":"Edificio premium con cochera y amenities.","fotos":["/img/"+base64.urlsafe_b64encode(b"https://inmobiliariatorres.com.ar/fotos_portada/22030.jpg").decode()+"/"+base64.urlsafe_b64encode(b"https://inmobiliariatorres.com.ar").decode()],"url":"https://inmobiliariatorres.com.ar/2023/ficha.php?id=1550","inm":"Torres Inmobiliaria","email":"info@inmobiliariatorres.com.ar","tel":"+543764533092"},
      {"f":"Torres Inmobiliaria","t":"Casa","tit":"Casa 5 dorms — Av. Centenario","b":"Posadas","d":"Av Centenario N°2954","p":280000,"m":"USD","m2":310,"dorms":5,"banos":4,"desc":"Amplia propiedad familiar, doble cochera, pileta.","fotos":[],"url":"https://inmobiliariatorres.com.ar","inm":"Torres Inmobiliaria","email":"info@inmobiliariatorres.com.ar","tel":"+543764533092"},
      {"f":"Torres Inmobiliaria","t":"Departamento","tit":"Hermoso dpto 3 dorms — pleno centro","b":"Centro","d":"Tucumán N°1912","p":120000,"m":"USD","m2":110,"dorms":3,"banos":2,"desc":"Departamento centric, 2 cocheras, muy luminoso.","fotos":[],"url":"https://inmobiliariatorres.com.ar","inm":"Torres Inmobiliaria","email":"info@inmobiliariatorres.com.ar","tel":"+543764533092"},
      # Daviña
      {"f":"Daviña Inmobiliaria","t":"Departamento","tit":"Dpto exclusivo en Costanera de Posadas","b":"Costanera","d":"Av. Roque Sáenz Peña 2300","p":225000,"m":"USD","m2":125,"dorms":2,"banos":3,"desc":"200m del río, 2 dorms en suite, vista al Paraná.","fotos":["/img/"+base64.urlsafe_b64encode(b"https://davinia.com.ar/wp-content/uploads/2026/01/Departamento-exclusivo-en-Costanera-de-Posadas-14.jpeg").decode()+"/"+base64.urlsafe_b64encode(b"https://davinia.com.ar").decode()],"url":"https://davinia.com.ar/property/departamento-exclusivo-en-costanera-de-posadas/","inm":"Daviña Inmobiliaria","email":"davinainmobiliaria@gmail.com","tel":"+5493764425983"},
      {"f":"Daviña Inmobiliaria","t":"Casa","tit":"Casa 3 plantas — Barrio Villa Sarita","b":"Villa Sarita","d":"Hernández 2473","p":195000,"m":"USD","m2":428,"dorms":4,"banos":3,"desc":"Histórico barrio de Villa Sarita, propiedad de gran categoría.","fotos":[],"url":"https://davinia.com.ar","inm":"Daviña Inmobiliaria","email":"davinainmobiliaria@gmail.com","tel":"+5493764425983"},
      {"f":"Daviña Inmobiliaria","t":"Terreno","tit":"Lote en esquina — Av. Cabred","b":"Villa Urquiza","d":"Av. Domingo Cabred & Perito Moreno","p":110000,"m":"USD","m2":300,"dorms":0,"banos":0,"desc":"Esquina con potencial constructivo.","fotos":[],"url":"https://davinia.com.ar","inm":"Daviña Inmobiliaria","email":"davinainmobiliaria@gmail.com","tel":"+5493764425983"},
      # Sosa
      {"f":"Sosa Inmobiliaria","t":"Casa","tit":"Casa 3 dorms — esquina Monseñor de Andrea","b":"Posadas","d":"Av. Monseñor de Andrea y Calle 122","p":680000000,"m":"ARS","m2":180,"dorms":3,"banos":3,"desc":"Propiedad de lujo en esquina, suite con jacuzzi.","fotos":["/img/"+base64.urlsafe_b64encode(b"https://cdn-images.xintelweb.com/upload/sos5090_2.jpg").decode()+"/"+base64.urlsafe_b64encode(b"https://www.sosainmobiliaria.com").decode()],"url":"https://www.sosainmobiliaria.com","inm":"Sosa Inmobiliaria","email":"","tel":"+5493764422483"},
      {"f":"Sosa Inmobiliaria","t":"Departamento","tit":"Dpto studio Centro — inversión de pozo","b":"Centro","d":"Santa Fe N°1744","p":95000,"m":"USD","m2":45,"dorms":1,"banos":1,"desc":"Edificio Merak, desarrollo de vanguardia, inversión desde el pozo.","fotos":[],"url":"https://www.sosainmobiliaria.com","inm":"Sosa Inmobiliaria","email":"","tel":"+5493764422483"},
      {"f":"Sosa Inmobiliaria","t":"Local","tit":"Local en Centro sobre Alvear","b":"Centro","d":"Alvear 2021","p":390000000,"m":"ARS","m2":80,"dorms":0,"banos":1,"desc":"Local comercial zona estratégica, alta visibilidad.","fotos":[],"url":"https://www.sosainmobiliaria.com","inm":"Sosa Inmobiliaria","email":"","tel":"+5493764422483"},
      # Mónica Fogeler
      {"f":"Mónica Fogeler","t":"Terreno","tit":"Terreno en pleno centro — calle importante","b":"Centro","d":"Calle Colón al 1200","p":250000,"m":"USD","m2":400,"dorms":0,"banos":0,"desc":"Terreno en importante calle del centro de Posadas.","fotos":["/img/"+base64.urlsafe_b64encode(b"https://cdn-images.xintelweb.com/upload/mof1194_2.jpg?218934").decode()+"/"+base64.urlsafe_b64encode(b"https://www.monicafogeler.com.ar").decode()],"url":"https://www.monicafogeler.com.ar","inm":"Mónica Fogeler","email":"monica.fogeler@gmail.com","tel":"+5493764129799"},
      {"f":"Mónica Fogeler","t":"Casa","tit":"Casa 4 dorms en el Centro de Posadas","b":"Centro","d":"Centro de Posadas","p":250000,"m":"USD","m2":506,"dorms":4,"banos":3,"desc":"Excelente ubicación, en el centro de Posadas.","fotos":["/img/"+base64.urlsafe_b64encode(b"https://cdn-images.xintelweb.com/upload/587d89a76df019a14e6ff5bc6d19b88e.jpg?91019").decode()+"/"+base64.urlsafe_b64encode(b"https://www.monicafogeler.com.ar").decode()],"url":"https://www.monicafogeler.com.ar","inm":"Mónica Fogeler","email":"monica.fogeler@gmail.com","tel":"+5493764129799"},
      {"f":"Mónica Fogeler","t":"Departamento","tit":"Dpto Torre del Sol II — 2 dorms","b":"Centro","d":"Torre del Sol II, Centro","p":200000,"m":"USD","m2":87,"dorms":2,"banos":2,"desc":"Edificio de categoría Torre del Sol II, 2 dorms.","fotos":[],"url":"https://www.monicafogeler.com.ar","inm":"Mónica Fogeler","email":"monica.fogeler@gmail.com","tel":"+5493764129799"},
      # Singular
      {"f":"Singular Inmobiliaria","t":"Departamento","tit":"Dpto 2 dorms — Singular Posadas","b":"Centro","d":"La Rioja 1578, Posadas","p":260000,"m":"USD","m2":90,"dorms":2,"banos":2,"desc":"Inmobiliaria + Ingeniería en Posadas, Misiones.","fotos":[],"url":"https://www.inmobiliariamasingenieria.com.ar","inm":"Singular Inmobiliaria","email":"singular@inmobiliariamasingenieria.com.ar","tel":"+5493764724379"},
      # Solari
      {"f":"Solari Bienes Raíces","t":"Departamento","tit":"Dpto 2 dorms a estrenar — Edificio Torre Sol","b":"Centro","d":"Colón 1350","p":89000,"m":"USD","m2":65,"dorms":2,"banos":2,"desc":"A estrenar, cochera, amenities, balcón.","fotos":[],"url":"https://www.facebook.com/solari.bienesraices/","inm":"Solari Bienes Raíces","email":"info@solaribienesraices.com.ar","tel":"+5493764439998"},
      {"f":"Solari Bienes Raíces","t":"Casa","tit":"Casa 3 dorms con cochera doble — Villa Cabello","b":"Villa Cabello","d":"Alvear 2890","p":135000,"m":"USD","m2":190,"dorms":3,"banos":2,"desc":"Casa de categoría, jardín, cochera doble.","fotos":[],"url":"https://www.facebook.com/solari.bienesraices/","inm":"Solari Bienes Raíces","email":"info@solaribienesraices.com.ar","tel":"+5493764439998"},
      # Fénix
      {"f":"Fénix Inmobiliaria","t":"Casa","tit":"Casa 3 dorms — Fénix Posadas","b":"Posadas","d":"Posadas, Misiones","p":95000,"m":"USD","m2":160,"dorms":3,"banos":2,"desc":"Inmobiliaria Fénix, más de 10 años en Posadas.","fotos":[],"url":"https://fenixxweb.com","inm":"Fénix Inmobiliaria","email":"info@fenixxweb.com","tel":"+5493764555111"},
      {"f":"Fénix Inmobiliaria","t":"Terreno","tit":"Terreno escriturado 400m² — Peñaflor","b":"Peñaflor","d":"Calle Entre Ríos 1100","p":28000,"m":"USD","m2":400,"dorms":0,"banos":0,"desc":"Lote 10x40 escriturado, todos los servicios.","fotos":[],"url":"https://fenixxweb.com","inm":"Fénix Inmobiliaria","email":"info@fenixxweb.com","tel":"+5493764555111"},
      # Zapani
      {"f":"Zapani Inmobiliaria","t":"Departamento","tit":"Dpto 1 dorm — Estado de Israel","b":"Posadas","d":"Estado de Israel 4355","p":58000,"m":"USD","m2":42,"dorms":1,"banos":1,"desc":"Luminoso, ideal primera vivienda.","fotos":[],"url":"https://www.inmobiliariazapani.com.ar","inm":"Zapani Inmobiliaria","email":"inmobiliariazapani@hotmail.com","tel":"+5493764436113"},
      {"f":"Zapani Inmobiliaria","t":"Casa","tit":"Casa 3 dorms zona Maipú — Zapani","b":"Posadas","d":"Av. Maipú al 4300","p":98000,"m":"USD","m2":220,"dorms":3,"banos":2,"desc":"Casa familiar con terreno amplio.","fotos":[],"url":"https://www.inmobiliariazapani.com.ar","inm":"Zapani Inmobiliaria","email":"inmobiliariazapani@hotmail.com","tel":"+5493764436113"},
      # Innova
      {"f":"Innova Inmobiliaria","t":"Casa","tit":"Casa en Posadas — Innova","b":"Posadas","d":"Bolívar 1521, Posadas","p":85000,"m":"USD","m2":140,"dorms":3,"banos":2,"desc":"Servicio personalizado y seguimiento constante.","fotos":[],"url":"https://innovaservicioinmobiliario.com","inm":"Innova Inmobiliaria","email":"innovaserv.inmobiliarios@gmail.com","tel":"+5493764150099"},
    ]
    props=[]
    for i,s in enumerate(SEED):
        lat,lng=geo_fallback(s["b"]+" "+s.get("d",""))
        props.append(mk(id=pid("seed",f"{i}{s['tit'][:15]}"),ref=f"SEED-{i:03d}",
            fuente=s["f"],tipo=s["t"],titulo=s["tit"],barrio=s["b"],dir=s.get("d",""),
            precio=s["p"],moneda=s["m"],m2=s.get("m2"),dorms=s.get("dorms"),
            banos=s.get("banos"),desc=s.get("desc",""),fotos=s.get("fotos",[]),
            url=s.get("url",""),inm=s.get("inm",""),email=s.get("email",""),
            tel=s.get("tel",""),lat=lat,lng=lng))
    return props

# ── PERSISTENCIA ───────────────────────────────────────────────────────────
def guardar(props):
    global _MEM,_FECHA
    _MEM=props; _FECHA=datetime.now().isoformat()
    try: DATA_FILE.write_text(json.dumps(props,ensure_ascii=False,indent=2))
    except Exception as e: log.warning(f"Disco: {e}")

def cargar():
    global _MEM
    if _MEM: return _MEM
    if DATA_FILE.exists():
        try: _MEM=json.loads(DATA_FILE.read_text()); return _MEM
        except: pass
    return []

def necesita_refresh():
    if _MEM and _FECHA:
        return (datetime.now()-datetime.fromisoformat(_FECHA))>timedelta(hours=REFRESH_HOURS)
    return True

# ── FASTAPI ────────────────────────────────────────────────────────────────
app=FastAPI(title="InmoPosadas API",version="4.0.0")
app.add_middleware(CORSMiddleware,allow_origins=["*"],allow_methods=["*"],allow_headers=["*"])

# ── PROXY DE IMÁGENES ──────────────────────────────────────────────────────
@app.get("/img/{img_b64}/{ref_b64}")
async def img_proxy(img_b64:str, ref_b64:str):
    """
    Proxy que descarga imágenes con el Referer correcto (evita hotlink protection).
    Las imágenes se cachean 24h en el navegador del usuario.
    """
    try:
        img_url = base64.urlsafe_b64decode(img_b64.encode()).decode()
        referer = base64.urlsafe_b64decode(ref_b64.encode()).decode()
    except:
        return Response(status_code=400)
    try:
        async with httpx.AsyncClient(timeout=15.0,follow_redirects=True) as cl:
            r=await cl.get(img_url,headers={
                "User-Agent":UA,
                "Referer":referer,
                "Accept":"image/webp,image/apng,image/*,*/*;q=0.8",
            })
            if r.status_code!=200:
                return Response(status_code=404)
            ct=r.headers.get("content-type","image/jpeg")
            return Response(
                content=r.content,
                media_type=ct,
                headers={"Cache-Control":"public, max-age=86400",
                         "Access-Control-Allow-Origin":"*"}
            )
    except:
        return Response(status_code=502)

@app.on_event("startup")
async def startup():
    global _SCRAPING
    if not cargar():
        guardar(semilla_props()); log.info("Semilla cargada")
    if not _SCRAPING:
        _SCRAPING=True; asyncio.create_task(_tarea())

async def _tarea():
    global _SCRAPING
    try: props=await ejecutar_scraping(); guardar(props)
    finally: _SCRAPING=False

@app.get("/health")
async def health():
    return {"status":"ok","propiedades":len(_MEM),"ts":datetime.now().isoformat()}

@app.get("/api/status")
async def api_status():
    return {"scraping_en_curso":_SCRAPING,"ultima_actualizacion":_FECHA or None,
            "total_propiedades":len(_MEM),"datos_disponibles":bool(_MEM)}

@app.post("/api/refresh")
async def refresh(bg:BackgroundTasks):
    global _SCRAPING
    if _SCRAPING: return {"message":"Scraping en curso."}
    _SCRAPING=True; bg.add_task(_tarea)
    return {"message":"Scraping iniciado (2-5 min)."}

@app.get("/api/propiedades")
async def get_props(
    tipo:Optional[str]=Query(None),barrio:Optional[str]=Query(None),
    moneda:Optional[str]=Query(None),precio_min:Optional[int]=Query(None),
    precio_max:Optional[int]=Query(None),dormitorios:Optional[int]=Query(None),
    fuente:Optional[str]=Query(None),limit:int=Query(500),offset:int=Query(0),
):
    ps=cargar()
    if tipo:       ps=[p for p in ps if p["tipo"].lower()==tipo.lower()]
    if barrio:     ps=[p for p in ps if barrio.lower() in p["barrio"].lower()]
    if moneda:     ps=[p for p in ps if p["moneda"].upper()==moneda.upper()]
    if precio_min: ps=[p for p in ps if p["precio"]>=precio_min]
    if precio_max: ps=[p for p in ps if p["precio"]<=precio_max]
    if dormitorios:ps=[p for p in ps if p.get("dormitorios")==dormitorios]
    if fuente:     ps=[p for p in ps if fuente.lower() in p["fuente"].lower()]
    return {"total":len(ps),"resultados":ps[offset:offset+limit]}

@app.get("/api/estadisticas")
async def stats():
    ps=cargar()
    if not ps: return {"total":0}
    usd=[p["precio"] for p in ps if p["moneda"]=="USD" and p["precio"]>0]
    tipos,fuentes,barrios={},{},{}
    for p in ps:
        tipos[p["tipo"]]=tipos.get(p["tipo"],0)+1
        fuentes[p["fuente"]]=fuentes.get(p["fuente"],0)+1
        barrios[p["barrio"]]=barrios.get(p["barrio"],0)+1
    return{"total":len(ps),
           "precio_usd_min":min(usd) if usd else 0,
           "precio_usd_max":max(usd) if usd else 0,
           "precio_usd_promedio":int(sum(usd)/len(usd)) if usd else 0,
           "por_tipo":tipos,"por_fuente":fuentes,
           "top_barrios":dict(sorted(barrios.items(),key=lambda x:-x[1])[:10])}

FRONTEND=BASE_DIR/"frontend"
if FRONTEND.exists():
    app.mount("/",StaticFiles(directory=str(FRONTEND),html=True),name="static")
