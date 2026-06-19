#!/usr/bin/env python3
"""
PUBLICAR.py — Script de publicación automática de InmoPosadas
Ejecutá: python3 PUBLICAR.py
"""
import subprocess, sys, json, time, urllib.request, urllib.error, os

REPO_NAME = "inmopo-posadas"
APP_NAME  = "inmopo-posadas"

def color(txt, c):
    codes = {"verde":"\033[92m","amarillo":"\033[93m","rojo":"\033[91m","azul":"\033[94m","bold":"\033[1m","reset":"\033[0m"}
    return f"{codes.get(c,'')}{txt}{codes['reset']}"

def titulo(txt):
    print(f"\n{color('━'*55,'azul')}")
    print(f"{color(f'  {txt}','bold')}")
    print(f"{color('━'*55,'azul')}")

def ok(txt):   print(f"  {color('✅','verde')} {txt}")
def info(txt): print(f"  {color('ℹ','azul')} {txt}")
def warn(txt): print(f"  {color('⚠','amarillo')} {txt}")
def err(txt):  print(f"  {color('✗ ERROR:','rojo')} {txt}")

def gh_request(endpoint, method="GET", data=None, token=None):
    url = f"https://api.github.com{endpoint}"
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"token {token}")
    req.add_header("Accept", "application/vnd.github.v3+json")
    req.add_header("Content-Type", "application/json")
    if data:
        req.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return json.loads(body) if body else {}, e.code

def main():
    print(f"\n{color('🏡 InmoPosadas — Publicación automática','bold')}")
    print(f"{color('   Posadas, Misiones · Sistema de mapa inmobiliario','azul')}\n")

    # ── PASO 1: TOKEN GITHUB ───────────────────────────────────────────────
    titulo("PASO 1 — Token de GitHub")
    print("""
  Para publicar necesitás un token de GitHub (tarda 2 minutos crearlo):

  1. Abrí en el navegador:
     https://github.com/settings/tokens/new

  2. Completá:
     • Note: inmopo-deploy
     • Expiration: 7 days
     • Scopes: tildá ✅ "repo" (primera opción)

  3. Hacé clic en "Generate token"
  4. Copiá el token (empieza con ghp_...)
""")
    token = input(color("  Pegá tu token aquí: ","amarillo")).strip()
    if not token.startswith("gh"):
        err("El token no parece válido. Debe empezar con 'ghp_' o 'github_pat_'")
        sys.exit(1)

    # Verificar token
    user_data, status = gh_request("/user", token=token)
    if status != 200:
        err(f"Token inválido (HTTP {status})")
        sys.exit(1)
    username = user_data["login"]
    ok(f"Token válido · Usuario: {color(username,'verde')}")

    # ── PASO 2: CREAR REPOSITORIO ──────────────────────────────────────────
    titulo("PASO 2 — Crear repositorio en GitHub")

    # Ver si ya existe
    existing, st = gh_request(f"/repos/{username}/{REPO_NAME}", token=token)
    if st == 200:
        warn(f"El repositorio '{REPO_NAME}' ya existe — usando el existente")
        repo_url = existing["clone_url"]
        html_url = existing["html_url"]
    else:
        info("Creando repositorio...")
        repo_data, st2 = gh_request("/user/repos", method="POST", token=token, data={
            "name": REPO_NAME,
            "description": "🏡 Mapa inmobiliario de Posadas, Misiones — Sistema de scraping + mapa interactivo",
            "private": False,
            "auto_init": False,
        })
        if st2 not in (200, 201):
            err(f"No se pudo crear el repositorio: {repo_data.get('message','')}")
            sys.exit(1)
        repo_url = repo_data["clone_url"]
        html_url = repo_data["html_url"]
        ok(f"Repositorio creado: {html_url}")

    # ── PASO 3: PUSH ───────────────────────────────────────────────────────
    titulo("PASO 3 — Subir código a GitHub")

    # Construir URL con credenciales
    push_url = repo_url.replace("https://", f"https://{username}:{token}@")

    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Configurar remote
    subprocess.run(["git", "remote", "remove", "origin"], capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", push_url], capture_output=True)

    info("Subiendo código...")
    result = subprocess.run(
        ["git", "push", "-u", "origin", "main", "--force"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        err(f"Error en push: {result.stderr}")
        sys.exit(1)
    ok(f"Código subido a GitHub: {html_url}")

    # ── PASO 4: RENDER ─────────────────────────────────────────────────────
    titulo("PASO 4 — Publicar en Render")
    print(f"""
  El código ya está en GitHub. Ahora publicalo en Render:

  1. Abrí: {color('https://render.com','azul')}
  2. Clic en "Get Started for Free" → registrate con GitHub
  3. Clic en "New +" → "Web Service"
  4. Conectá el repo: {color(html_url,'azul')}
  5. Completá estos campos EXACTOS:

     {color('Runtime:','amarillo')}       Python 3
     {color('Build Command:','amarillo')} pip install -r backend/requirements.txt
     {color('Start Command:','amarillo')} uvicorn backend.main:app --host 0.0.0.0 --port $PORT
     {color('Instance Type:','amarillo')} Free

  6. Clic en "Create Web Service"
  7. Esperá 5-10 minutos → tu URL va a aparecer arriba

""")
    render_url = input(color("  Cuando Render te dé la URL, pegala acá (o Enter para saltar): ","amarillo")).strip()

    # ── PASO 5: UPTIMEROBOT ────────────────────────────────────────────────
    titulo("PASO 5 — Mantener activa con UptimeRobot")
    if render_url:
        health_url = render_url.rstrip("/") + "/health"
        print(f"""
  Configurá UptimeRobot para que la web nunca se duerma:

  1. Abrí: {color('https://uptimerobot.com','azul')}
  2. Registrate gratis
  3. Clic en "+ Add New Monitor"
  4. Monitor Type: HTTP(s)
  5. URL: {color(health_url,'verde')}
  6. Interval: 5 minutes
  7. Clic "Create Monitor"
""")
    else:
        print(f"""
  Cuando tengas la URL de Render, configurá UptimeRobot:
  URL del monitor: https://TU-APP.onrender.com/health
""")

    # ── RESUMEN FINAL ──────────────────────────────────────────────────────
    titulo("✅ ¡LISTO!")
    ok(f"Código en GitHub:  {html_url}")
    if render_url:
        ok(f"Web en internet:   {color(render_url,'verde')}")
        ok(f"API de estado:     {render_url}/api/status")
        ok(f"Monitor de salud:  {render_url}/health")
    print(f"""
  {color('Tu web de inmuebles de Posadas está en línea.','bold')}
  Cualquier persona puede visitarla desde el celular o computadora.
  Los datos se actualizan automáticamente cada 6 horas.
""")

if __name__ == "__main__":
    main()
