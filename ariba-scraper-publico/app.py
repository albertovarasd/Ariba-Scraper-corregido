import asyncio
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from file_filter import should_ignore
from dom_selectors import SELECTORS
from google_io import GoogleIo
from datetime import datetime
import zipfile
import shutil

# --- Forzar salida en UTF-8 (arregla el crash por emojis) ---
# Cuando este script corre con la salida redirigida a un archivo de log
# (como hace ejecutar_diario.bat, o el Programador de Tareas de Windows),
# Python usa por defecto la codificación antigua de la consola de Windows
# (cp1252), que NO sabe representar emojis (🔍, ⚠️, ✅, etc.) — y el programa
# se caía con UnicodeEncodeError apenas intentaba imprimir el primero.
# En una terminal interactiva normal no pasaba porque PowerShell sí entiende
# UTF-8. Esto fuerza UTF-8 siempre, sin importar el contexto de ejecución.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

load_dotenv()

# --- Soporte opcional para archivos .rar ---
# Se importa de forma "perezosa" (try/except) para que el programa NO se caiga
# si el paquete 'rarfile' no está instalado en el entorno (esta era la causa
# más probable de que funcionara en un equipo/entorno y en otro no).
try:
    import rarfile
    RARFILE_AVAILABLE = True
    # Si existe un unrar.exe embebido junto al script (Windows), se usa ese
    # en vez de depender de que 'unrar' esté en el PATH del sistema.
    # OJO: usar SIEMPRE la versión de CONSOLA de UnRAR (sin ventanas), nunca
    # una versión "GUI" — una versión GUI abre su propia ventana pidiendo
    # "Extraer" y se queda esperando a que alguien la cierre a mano, lo que
    # trababa por completo las corridas automáticas/desatendidas.
    _bundled_unrar = Path(__file__).parent / "UnRAR.exe"
    if _bundled_unrar.exists():
        rarfile.UNRAR_TOOL = str(_bundled_unrar)
except ImportError:
    RARFILE_AVAILABLE = False
    print("⚠️ Paquete 'rarfile' no instalado: los .rar se subirán sin descomprimir. "
          "Ejecuta 'pip install rarfile' para habilitar la extracción.")

# Variables de ambiente
ARIBA_LOGIN_URL = os.getenv("ARIBA_LOGIN_URL")
ARIBA_EMAIL = os.getenv("ARIBA_EMAIL")
ARIBA_PASSWORD = os.getenv("ARIBA_PASSWORD")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
DOWNLOAD_TEMP = Path(os.getenv("DOWNLOAD_TEMP", "./downloads"))
DOWNLOAD_TEMP.mkdir(parents=True, exist_ok=True)
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# Comprador cuyas licitaciones se monitorean: texto que aparece en el menú de
# compradores de Ariba. COMPRADOR_PATRON es opcional y permite una expresión
# regular propia (por ejemplo "Empresa Demo|Demo S\.A\."); si no se define,
# se busca el nombre tal cual.
COMPRADOR_NOMBRE = (os.getenv("COMPRADOR_NOMBRE") or "").strip()
COMPRADOR_PATRON = os.getenv("COMPRADOR_PATRON") or re.escape(COMPRADOR_NOMBRE)

ALLOWED_EXTENSIONS = {'.pdf', '.dwg', '.xls', '.xlsx', '.ppt', '.pptx'}

def is_allowed(filename):
    return any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)

def process_archive(filepath, folder_id, g):
    """
    Descomprime un .zip o .rar y sube a Drive solo los archivos permitidos
    (ver ALLOWED_EXTENSIONS). Si la extracción falla o no aplica (p. ej. .rar
    sin 'rarfile'/unrar disponible), se sube el archivo comprimido tal cual
    para no perder el adjunto silenciosamente.
    """
    temp_dir = filepath.parent / f"extracted_{filepath.stem}"
    temp_dir.mkdir(exist_ok=True)
    extracted_files = []

    try:
        if filepath.suffix.lower() == '.zip':
            with zipfile.ZipFile(filepath, 'r') as archive:
                for member in archive.namelist():
                    if is_allowed(member):
                        archive.extract(member, temp_dir)
                        extracted_files.append(temp_dir / member)

        elif filepath.suffix.lower() == '.rar':
            if not RARFILE_AVAILABLE:
                raise RuntimeError("'rarfile' no está disponible en este entorno")
            with rarfile.RarFile(filepath, 'r') as archive:
                for member in archive.namelist():
                    if is_allowed(member):
                        archive.extract(member, temp_dir)
                        extracted_files.append(temp_dir / member)

    except Exception as e:
        print(f"⚠️ No se pudo descomprimir '{filepath.name}' ({e}). "
              f"Subiendo el archivo comprimido sin descomprimir.")
        g.upload_file(filepath, folder_id)
        shutil.rmtree(temp_dir, ignore_errors=True)
        return

    if not extracted_files:
        # El archivo se descomprimió pero no había nada permitido adentro
        # (o el archivo venía vacío): subir el original igual, por seguridad.
        print(f"⚠️ No se encontraron archivos permitidos dentro de '{filepath.name}'. "
              f"Subiendo el archivo comprimido original como respaldo.")
        g.upload_file(filepath, folder_id)
    else:
        for file in extracted_files:
            g.upload_file(file, folder_id)
            print(f"Archivo extraído y subido a Drive: {file}")

    shutil.rmtree(temp_dir, ignore_errors=True)

# -----------------------
# Login / Web scraping
# -----------------------
def validar_configuracion():
    """Falla de inmediato, con un mensaje claro, si falta alguna variable del .env."""
    requeridas = {
        "ARIBA_LOGIN_URL": ARIBA_LOGIN_URL,
        "ARIBA_EMAIL": ARIBA_EMAIL,
        "ARIBA_PASSWORD": ARIBA_PASSWORD,
        "GOOGLE_SERVICE_ACCOUNT_FILE": GOOGLE_SERVICE_ACCOUNT_FILE,
        "GOOGLE_SHEET_ID": GOOGLE_SHEET_ID,
        "GOOGLE_DRIVE_FOLDER_ID": GOOGLE_DRIVE_FOLDER_ID,
        "COMPRADOR_NOMBRE": COMPRADOR_NOMBRE,
    }
    faltantes = [nombre for nombre, valor in requeridas.items() if not valor]
    if faltantes:
        raise SystemExit(
            "Faltan variables en el archivo .env: " + ", ".join(faltantes) +
            "\nCopia .env.example a .env y completa los valores."
        )


async def scrap():
    validar_configuracion()
    nuevas_filas = 0
    start_time = time.time()
    # Cargar configuración
    g = GoogleIo(GOOGLE_SERVICE_ACCOUNT_FILE, sheet_id=GOOGLE_SHEET_ID, root_folder_id=GOOGLE_DRIVE_FOLDER_ID)    

    execution_date = datetime.now().strftime("%d/%m/%y")

    async with async_playwright() as p:
        # OJO: en modo HEADLESS=true (usado en el servidor) el navegador puede
        # comportarse distinto a HEADLESS=false — se detectó que con el mismo
        # código, misma hoja y mismos registros faltantes, el modo headless
        # dejó de capturar licitaciones que sí capturaba en modo visible.
        # La causa más probable es el tamaño de "ventana" (viewport): la
        # tabla de scroll virtual de Ariba puede calcular cuántas filas
        # renderizar según ese tamaño, y el valor por defecto puede diferir
        # entre ambos modos. Se fuerza aquí un tamaño grande y CONSISTENTE en
        # ambos casos, para que la tabla se comporte igual sin importar el
        # modo de ejecución.
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=["--disable-blink-features=AutomationControlled", "--window-size=1920,1080"]
        )
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})

        # --- Forzar descarga (en vez de vista previa) para archivos de imagen ---
        # Algunos adjuntos (ej. .tif) el navegador los intenta MOSTRAR en vez de
        # descargarlos, si el servidor no indica explícitamente "esto es un
        # archivo para guardar" (encabezado Content-Disposition: attachment).
        # Cuando eso pasa, el código nunca recibe el evento de "descarga" que
        # espera, y el archivo se pierde en silencio. Esto intercepta la
        # respuesta y agrega ese encabezado si falta, para imágenes.
        async def _forzar_descarga_imagenes(route, request):
            try:
                response = await route.fetch()
                headers = {k.lower(): v for k, v in response.headers.items()}
                content_type = headers.get("content-type", "")
                if content_type.startswith("image/") and "content-disposition" not in headers:
                    headers["content-disposition"] = "attachment"
                    await route.fulfill(response=response, headers=headers)
                else:
                    await route.fulfill(response=response)
            except Exception:
                # Si algo falla en la intercepción, dejar pasar la solicitud
                # normal en vez de romper toda la navegación.
                await route.continue_()

        await context.route("**/s3.ariba.com/**", _forzar_descarga_imagenes)
        await context.route("**/*.tif", _forzar_descarga_imagenes)
        await context.route("**/*.tiff", _forzar_descarga_imagenes)

        page = await context.new_page()

        print("Abriendo página de login...")
        await page.goto(ARIBA_LOGIN_URL, timeout=180000)

        print("Ingresando email...")
        await page.wait_for_selector(SELECTORS["username"], timeout=120000)
        await page.fill(SELECTORS["username"], ARIBA_EMAIL)
        await page.wait_for_selector(SELECTORS["username_button"], timeout=60000)
        await page.locator(SELECTORS["username_button"]).click()

        print("Ingresando password...")
        await page.wait_for_selector(SELECTORS["password"], timeout=180000)
        await page.fill(SELECTORS["password"], ARIBA_PASSWORD)
        await asyncio.sleep(3)
        await page.locator(SELECTORS["password_button"]).click()

        print("Esperando redirección post-login...")
        await page.wait_for_load_state("domcontentloaded", timeout=60000)
        await asyncio.sleep(5)
        print(f"URL actual post-login: {page.url}")
        print(f"Título de página: {await page.title()}")

        # --- NUEVO BLOQUE: detección condicional de cookies y popup ---
        print("Verificando si aparece banner de aceptación de cookies...")
        try:
            await page.wait_for_selector(SELECTORS["cookie_accept"], timeout=10000)
            print("Banner de cookies detectado. Presionando 'Aceptar todo'...")
            await page.locator(SELECTORS["cookie_accept"]).click()
            await asyncio.sleep(2)
        except Exception:
            print("No apareció ningún banner de cookies (o ya fue aceptado).")

        print("Verificando si aparece popup de publicidad...")
        try:
            await page.wait_for_selector(SELECTORS["ad_popup_close"], timeout=10000)
            print("Popup detectado. Cerrando...")
            await page.locator(SELECTORS["ad_popup_close"]).click()
            await asyncio.sleep(1)
        except Exception:
            print("No apareció ningún popup. Continuando con el proceso...")

        print("Esperando para abrir menu superior...")
        try:
            await page.wait_for_selector(SELECTORS["upper_menu"], timeout=60000)
        except Exception as err:
            await page.screenshot(path="login_debug.png", full_page=True)
            print(f"❌ Error al buscar el menú superior. URL: {page.url}")
            print(f"📸 Se guardó una captura de pantalla del estado actual en 'login_debug.png'")
            raise err

        # Re-verificar si el banner de cookies interfiere antes de hacer click
        try:
            cookie_btn = page.locator(SELECTORS["cookie_accept"])
            if await cookie_btn.is_visible():
                await cookie_btn.click()
                await asyncio.sleep(1)
        except Exception:
            pass

        await asyncio.sleep(3)
        await page.locator(SELECTORS["upper_menu"]).click()


        await asyncio.sleep(5)
        print("Esperando para seleccionar elemento de propuestas...")
        await page.wait_for_selector(SELECTORS["list_item"], timeout=180000)
        await asyncio.sleep(5)

        # Este clic dispara una navegación (a veces con varios saltos de OAuth/
        # redirecciones) que puede tardar más de lo normal si el servidor o la
        # red están más lentos ese día — un solo timeout aislado aquí no debería
        # tirar abajo todo el programa. Se reintenta con más margen antes de
        # rendirse.
        clic_exitoso = False
        for intento_menu in range(1, 4):
            try:
                await page.locator(SELECTORS["list_item"]).click(timeout=60000)
                clic_exitoso = True
                break
            except Exception as e:
                print(f"⚠️ Timeout/erro al hacer clic en el menú de propuestas (intento {intento_menu}): {e}")
                if intento_menu < 3:
                    await asyncio.sleep(5)

        if not clic_exitoso:
            raise RuntimeError(
                "No se pudo hacer clic en 'Ariba Proposals & Questionnaires' tras 3 "
                "intentos. Probablemente el servidor/red estaba muy lento en ese "
                "momento. Revisa la conexión e intenta correr el script de nuevo."
            )

        await asyncio.sleep(10)


        print("Esperando para presionar menu secundario...")
        comprador_seleccionado = False
        for intento_comprador in range(1, 4):
            try:
                dropdown_locator = page.locator('div.w-dashboard-tabs a.w-tabitem-a, a[bh="PML"]').first
                await dropdown_locator.wait_for(state="visible", timeout=30000)
                box = await dropdown_locator.bounding_box()
                if box:
                    await page.mouse.move(box["x"] + box["width"]/2, box["y"] + box["height"]/2)
                    print(f"Presionando menu secundario... (intento {intento_comprador})")
                    await page.mouse.down()
                    await asyncio.sleep(1)
                    await page.mouse.up()
                    await asyncio.sleep(3)

                # Imprimir opciones disponibles en el menú de compradores para depuración
                await asyncio.sleep(2)
                menu_items = await page.locator('a.w-pmi-item, div.awmenu a').all()
                found_buyers = []
                for item in menu_items:
                    t = await item.text_content()
                    if t:
                        found_buyers.append(t.strip())
                print(f"🔍 Opciones encontradas en el menú de compradores ({len(found_buyers)}): {found_buyers}")

                # Buscar la opción del comprador configurado (COMPRADOR_NOMBRE) dentro del menú emergente
                comprador_item = page.locator('a.w-pmi-item, div.awmenu a', has_text=re.compile(COMPRADOR_PATRON, re.IGNORECASE)).first
                if await comprador_item.count() > 0:
                    print(f"Seleccionando comprador: {COMPRADOR_NOMBRE}...")
                    await comprador_item.wait_for(state="visible", timeout=10000)
                    await comprador_item.click()
                    await asyncio.sleep(8)
                    comprador_seleccionado = True
                    break
                else:
                    print(f"⚠️ No se desplegó o no se encontró '{COMPRADOR_NOMBRE}' en el menú (intento {intento_comprador}).")

            except Exception as e:
                print(f"⚠️ Error durante la selección del comprador (intento {intento_comprador}): {e}")

            if intento_comprador < 3:
                print("   Reintentando abrir el menú de compradores...")
                await asyncio.sleep(3)

        if not comprador_seleccionado:
            # No seguir a ciegas: si no se pudo confirmar que se seleccionó
            # el comprador configurado, el resto del scraping leería datos del comprador
            # equivocado (o de una página en blanco) sin que nadie se dé
            # cuenta. Mejor cortar acá con un error claro.
            raise RuntimeError(
                f"No se pudo seleccionar '{COMPRADOR_NOMBRE}' en el menú de "
                "compradores después de 3 intentos. Abortando para no scrapear datos "
                "del comprador equivocado. Revisa 'table_debug.png' / vuelve a intentar."
            )

        # "networkidle" espera a que la red quede en silencio total — pero
        # sitios modernos como Ariba a veces tienen tráfico de fondo constante
        # (analítica, heartbeats, etc.) que nunca "se calla" del todo, lo que
        # puede hacer que esta espera falle por timeout aunque la página ya
        # esté completamente usable. Un timeout aislado acá no debería tirar
        # abajo todo el programa — se intenta, y si no se logra en 30s, se
        # sigue igual (los pasos siguientes ya tienen sus propias esperas).
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception as e:
            print(f"⚠️ No se alcanzó 'networkidle' en 30s (se continúa igual, probablemente la página ya está lista): {e}")


        print("Esperando iframe...")
        iframe_el = await page.wait_for_selector(SELECTORS["iframe"], timeout=120000)
        frame = await iframe_el.content_frame()
        if frame is None:
            raise RuntimeError("No se pudo obtener el content_frame del iframe")

        print("Cargando siguiente pagina...")
        await frame.wait_for_selector("body", timeout=120000)

        print("Antes de intentar click dentro del iframe...")
        clicked = False
        try:
            target_link = frame.locator(SELECTORS["active_proposals_link"]).first
            if await target_link.count() > 0 and await target_link.is_visible():
                await target_link.click(force=True)
                clicked = True
                print("✅ Clic exitoso en el enlace de propuestas activas.")
        except Exception as e:
            print(f"⚠️ No se encontró por selector exacto ({e}). Probando por texto...")

        if not clicked:
            for text_pattern in ["Propuestas", "Active", "Cuestionarios"]:
                loc = frame.locator("a", has_text=re.compile(text_pattern, re.IGNORECASE)).first
                if await loc.count() > 0 and await loc.is_visible():
                    try:
                        await loc.click(force=True)
                        clicked = True
                        print(f"✅ Clic realizado mediante texto '{text_pattern}'.")
                        break
                    except Exception:
                        continue

        if not clicked:
            print("⚠️ No se requirió hacer clic o no se encontró enlace secundario; continuando a la tabla...")

        await asyncio.sleep(5)

        # Captura de pantalla para inspeccionar el estado actual de la tabla dentro del iframe
        await page.screenshot(path="table_debug.png", full_page=True)
        print("📸 Captura de pantalla guardada en 'table_debug.png'")

        # --- Expandir SOLO el grupo de licitaciones abiertas ---
        # Ariba agrupa los eventos por estado y los muestra COLAPSADOS por defecto.
        # El ícono real de expandir/colapsar es <span class="w-togglebox-icon-off">
        # (cerrado) / <span class="w-togglebox-icon-on"> (abierto) — hacer clic en la
        # celda no lo activaba, hay que apuntarle directo a ese ícono.
        #
        # BILINGÜE: la interfaz de Ariba puede estar en español ("Estado: Abierto")
        # o en inglés ("Status: Abierto" / "Status: Open"), según la configuración
        # de idioma de la cuenta o del navegador. Se prueban todas las variantes
        # para que funcione sin importar el idioma.
        total_abiertas_esperadas = None
        _VARIANTES_ABIERTO = [
            "Estado: Abierto",   # español completo
            "Status: Abierto",   # interfaz en inglés, valor en español (visto en el servidor)
            "Status: Open",      # inglés completo
            "Estado: Open",      # mezcla inversa, por si acaso
        ]
        try:
            fila_abierto = None
            variante_encontrada = None
            for _variante in _VARIANTES_ABIERTO:
                _candidata = frame.locator("tr", has_text=_variante).first
                if await _candidata.count() > 0:
                    fila_abierto = _candidata
                    variante_encontrada = _variante
                    break

            if fila_abierto is not None:
                # Capturar el número entre paréntesis (ej: "Estado: Abierto (109)")
                # ANTES de expandir, para poder comparar al final cuántas se
                # procesaron realmente vs. cuántas dice Ariba que hay en total.
                texto_fila_abierto = await fila_abierto.text_content()
                match_total = re.search(r"(?:Estado|Status):\s*(?:Abierto|Open)\D*(\d+)",
                                        texto_fila_abierto or "", re.IGNORECASE)
                if match_total:
                    total_abiertas_esperadas = int(match_total.group(1))
                    print(f"🔢 Ariba reporta {total_abiertas_esperadas} licitaciones en '{variante_encontrada}'.")
                else:
                    print(f"⚠️ No se pudo leer el número total de '{variante_encontrada}' (formato inesperado).")

                toggle_icon = fila_abierto.locator(
                    "span.w-togglebox-icon-off, span.w-togglebox-icon-on"
                ).first
                if await toggle_icon.count() > 0:
                    await toggle_icon.click()
                    await asyncio.sleep(3)
                    print(f"🔽 Grupo '{variante_encontrada}' expandido.")
                else:
                    print(f"⚠️ No se encontró el ícono de expansión (w-togglebox-icon-off/on) en la fila '{variante_encontrada}'.")
            else:
                print(f"⚠️ No se encontró la fila de licitaciones abiertas para expandir. "
                      f"Variantes probadas: {_VARIANTES_ABIERTO}. "
                      f"Revisa 'table_debug.png' para ver cómo se ve la página.")
        except Exception as e:
            print(f"⚠️ No se pudo expandir el grupo de licitaciones abiertas: {e}")

        # Nueva captura ya con el grupo expandido, útil para depurar si algo falla
        await page.screenshot(path="table_debug_expandido.png", full_page=True)
        print("📸 Captura de pantalla (post-expansión) guardada en 'table_debug_expandido.png'")

        # Diagnóstico de estructura dentro del iframe
        all_tr = await frame.locator("tr").all()
        print(f"🔍 Total de filas 'tr' encontradas dentro del iframe: {len(all_tr)}")

        # Seleccionar todas las filas
        print("Iniciando extracción de filas con scroll virtual...")

        processed_ids = set()
        processed_urls = set()  # dedup adicional por URL (más confiable que el ID
                                 # cuando una fila del scroll virtual aún no terminó
                                 # de cargar y el ID sale "raro")
        entries = []
        col_ids = set(g.sheet.col_values(4))  # IDs existentes en Google Sheet
        col_urls = set(g.sheet.col_values(9))  # Links ya guardados (columna I) — permite
                                                # saltar de inmediato una fila ya conocida
                                                # SIN gastar los 4 reintentos de carga.
        descartadas_link_fantasma = 0
        descartadas_forma = 0
        descartadas_duplicado = 0
        filas_no_cargaron = 0

        # --- Varias PASADAS completas de scroll (arriba→abajo) ---
        # Con muchas filas cambiando a la vez, Ariba a veces tarda más de lo
        # que un solo reintento por fila puede cubrir. En vez de obligar al
        # usuario a correr el script de nuevo manualmente, se repite el
        # recorrido completo (bajando desde arriba) hasta MAX_PASADAS veces,
        # deteniéndose antes si dos pasadas seguidas no suman nada nuevo.
        # Con el corte inteligente de más abajo (para en cuanto se alcanza el
        # total esperado de "Estado: Abierto"), este techo casi nunca se
        # alcanza en la práctica — la mayoría de las corridas terminan en la
        # pasada 1 o 2. Solo actúa como red de seguridad cuando de verdad
        # faltan licitaciones por capturar.
        MAX_PASADAS = 5
        for numero_pasada in range(1, MAX_PASADAS + 1):
            print(f"\n🔁 Pasada de scroll #{numero_pasada} de {MAX_PASADAS}...")
            nuevos_antes_de_pasada = len(entries)

            # Volver al inicio del contenedor antes de cada pasada (menos en
            # la primera, que ya arranca arriba de por sí).
            if numero_pasada > 1:
                await frame.evaluate("""
                    (containerSelector) => {
                        const container = document.querySelector(containerSelector);
                        if (container) container.scrollTop = 0;
                    }
                """, SELECTORS["table_container"])
                await asyncio.sleep(3)

            while True:
                # Volvemos a incluir 'tr:has(a)' porque la tabla de licitaciones reales
                # no siempre usa las clases tableRow1/tableRow2 (Ariba reutiliza esos
                # nombres en varios widgets). En vez de filtrar por clase CSS, ahora
                # filtramos por CONTENIDO: descartamos filas cuyo link es "fantasma"
                # (apunta a la misma página, típico de acordeones/menús) y filas cuyo
                # texto no tiene forma de dato real (muy largo, con saltos de línea,
                # que es lo típico del HTML/JS de widgets de interfaz).
                rows = await frame.locator("tr.tableRow1, tr.tableRow2, tr[class*='tableRow'], tr:has(a)").all()

                current_frame_url = frame.url

                for row in rows:
                    try:
                        link = await row.evaluate("""row => {
                            const a = row.querySelector('a');
                            return a ? a.href : null;
                        }""")
                        if not link:
                            continue

                        # Descartar links "fantasma": los que apuntan a la MISMA
                        # página/ruta base que el frame actual (esto pasa con los
                        # <a href="#"> de los acordeones y filtros). Comparamos solo
                        # dominio + ruta (ignorando parámetros de sesión como
                        # 'awssk=...' que cambian en cada carga, y el fragmento #),
                        # porque comparar el string completo car-a-car casi nunca
                        # calza exacto aunque sea la misma página en la práctica.
                        _link_sin_query = urlparse(link)
                        _frame_sin_query = urlparse(current_frame_url)
                        if (_link_sin_query.netloc == _frame_sin_query.netloc
                                and _link_sin_query.path == _frame_sin_query.path):
                            descartadas_link_fantasma += 1
                            continue

                        # ⚡ Atajo de velocidad: el link (href) está disponible de
                        # inmediato, sin esperar a que la fila termine de cargar sus
                        # celdas de texto. Si este link YA está guardado en el Sheet
                        # (o ya se vio en esta misma corrida), es una licitación
                        # conocida — se salta directo, sin gastar los 4 reintentos
                        # de "¿ya cargó?" que sí hacen falta para filas GENUINAMENTE
                        # nuevas. Esto es lo que hacía tan lentas las pasadas 2, 3 y 4.
                        if link in col_urls or link in processed_urls:
                            descartadas_duplicado += 1
                            continue

                        # Extraer celdas td de la fila sin asumir estructuras anidadas rígidas
                        # OJO: cada celda visual de Ariba trae tablas ANIDADAS adentro
                        # (un <td> visible que contiene otro <table><td>...). Si se
                        # buscan todos los "td" del árbol, salen duplicados y los
                        # índices de columna se corren (por eso "Fecha de Cierre"
                        # salía vacía). ":scope > td" limita a los <td> hijos
                        # DIRECTOS de la fila, que son las columnas reales.
                        #
                        # REINTENTO REAL: en scroll virtual, una fila puede aparecer en
                        # el DOM ANTES de que Ariba termine de rellenar sus celdas por
                        # AJAX. En vez de saltarla y esperar (en vano) que el scroll la
                        # vuelva a mostrar más tarde, se relee esta MISMA fila hasta 4
                        # veces con una pequeña espera entre intentos.
                        entry_id = ""
                        name_val = ""
                        end_time_val = ""
                        cell_texts = []
                        fila_completa = False

                        for intento in range(4):
                            tds = await row.locator(":scope > td").all()
                            if not tds:
                                # Sin ni una sola celda <td>: esto NO es una fila de
                                # datos (probablemente basura de interfaz), nunca va
                                # a "terminar de cargar" por más que se espere.
                                # Cortar de inmediato en vez de gastar 4 reintentos.
                                break

                            cell_texts = []
                            for td in tds:
                                txt = (await td.text_content()).strip()
                                if txt:
                                    cell_texts.append(txt)

                            if not cell_texts:
                                # Igual que arriba: celdas vacías de entrada no son
                                # una fila real cargando, son basura de interfaz.
                                break

                            name_el = row.locator("a span, a").first
                            name_val = (await name_el.text_content()).strip() if await name_el.count() > 0 else cell_texts[0]
                            entry_id = cell_texts[1] if len(cell_texts) > 1 else cell_texts[0]
                            end_time_val = cell_texts[2] if len(cell_texts) > 2 else ""

                            def _normalizar(txt):
                                return re.sub(r"\s+", " ", txt).strip()

                            entry_id = _normalizar(entry_id)
                            name_val = _normalizar(name_val)

                            if entry_id != name_val and end_time_val:
                                fila_completa = True
                                break

                            # Acá SÍ hay contenido parcial (por ejemplo, ya se ve el
                            # título pero falta el ID/fecha) — esto es señal real de
                            # que la fila está cargando, vale la pena esperar y
                            # reintentar leyendo esta misma fila.
                            await asyncio.sleep(0.75)

                        if not cell_texts:
                            continue

                        if not fila_completa:
                            # Tras 4 intentos (~3 segundos) la fila sigue sin datos
                            # completos — recién ahí se descarta de verdad.
                            # Se muestra también el link (siempre disponible, aunque
                            # el texto de la fila no haya cargado) para poder
                            # identificar de qué licitación se trata si hace falta
                            # revisarla a mano.
                            print(f"   ↳ ⏳ fila no terminó de cargar tras varios intentos, se omite: "
                                  f"nombre={name_val[:60]!r} link={link}")
                            continue

                        # Palabras clave que solo aparecen en JS/HTML de widgets de
                        # interfaz (acordeones, choosers) o en la fila de encabezado
                        # de la tabla ("Título / ID / Hora de finalización..."),
                        # nunca en un título o ID real de licitación.
                        _MARCAS_BASURA = ("function(", "ariba.Dom", "RJS(", "ariba.Chooser",
                                           "ariba.AWWidgets", "removeRelocatedCopy",
                                           "Hora de finalización", "No hay artículos")

                        def _parece_basura(txt, max_len=300):
                            if not txt or len(txt) > max_len:
                                return True
                            if txt in ("Título", "ID", "Estado", "Tipo de evento", "Mercancía", "Regiones"):
                                return True
                            return any(marca in txt for marca in _MARCAS_BASURA)

                        if _parece_basura(entry_id) or _parece_basura(name_val):
                            descartadas_forma += 1
                            if descartadas_forma <= 5:
                                print(f"   ↳ (muestra descarte #{descartadas_forma}) id={entry_id[:80]!r} name={name_val[:80]!r}")
                            continue

                        # Evitar duplicados entre scrolls: por ID (ya guardado en el
                        # Sheet o ya visto en esta corrida) y por URL (más confiable,
                        # cubre el caso de una fila re-detectada con otro texto de ID).
                        if entry_id in processed_ids or entry_id in col_ids or link in processed_urls:
                            descartadas_duplicado += 1
                            continue

                        entry = {
                            "id": entry_id,
                            "url": link,
                            "name": name_val,
                            "end_time": end_time_val
                        }


                        processed_ids.add(entry_id)
                        processed_urls.add(link)
                        entries.append(entry)
                        print(f"✅ Nuevo registro detectado: {entry_id}")

                    except Exception as e:
                        print(f"⚠️ Error procesando fila: {e}")

                # Hacer scroll hacia abajo dentro del contenedor
                scrolled = await frame.evaluate("""
                    (containerSelector) => {
                        const container = document.querySelector(containerSelector);
                        if (!container) return true;  // si no se encuentra, consideramos que terminó

                        const before = container.scrollTop;
                        container.scrollBy(0, container.clientHeight * 0.9);
                        const after = container.scrollTop;

                        // Si el scrollTop no cambia, llegamos al final
                        return before === after;
                    }
                """, SELECTORS["table_container"])

                if scrolled:
                    print("✅ Fin del scroll detectado (contenedor al final).")
                    break

                await asyncio.sleep(5)  # Esperar carga de nuevas filas

            # ¿Cuánto llevamos hasta ahora? (nuevas de esta corrida + ya
            # conocidas de antes, que también cuentan como "capturadas").
            total_procesadas_hasta_ahora = len(entries) + descartadas_duplicado

            # Caso 1: ya tenemos todo lo que Ariba dice que hay — no hace
            # falta insistir con más pasadas, aunque queden intentos disponibles.
            if total_abiertas_esperadas is not None and total_procesadas_hasta_ahora >= total_abiertas_esperadas:
                print(f"   (pasada #{numero_pasada}: ya se cubrieron las {total_abiertas_esperadas} "
                      f"licitaciones esperadas, no hace falta seguir)")
                break

            # Caso 2: esta pasada no sumó NADA nuevo. Si además ya sabemos que
            # falta algo, vale la pena intentar una pasada más (el scroll
            # virtual puede comportarse distinto la segunda vez). Pero si ya
            # van 2 pasadas seguidas sin sumar nada, insistir más no va a
            # ayudar — se corta para no perder tiempo de más.
            if len(entries) == nuevos_antes_de_pasada:
                print(f"   (pasada #{numero_pasada} no sumó licitaciones nuevas)")
                if numero_pasada > 1:
                    break

        print(f"Total de registros nuevos: {len(entries)}")
        print(f"🔎 Diagnóstico de filas descartadas → link fantasma: {descartadas_link_fantasma}, "
              f"forma inválida (basura de interfaz): {descartadas_forma}, duplicadas: {descartadas_duplicado}")

        # --- Verificación de completitud ---
        # entries (nuevas) + duplicadas (ya reconocidas como licitaciones reales
        # ya guardadas) = total de licitaciones REALES que el script pudo
        # identificar en esta corrida. Esto se compara contra el número que
        # Ariba mostraba en "Estado: Abierto (XXX)" para saber si faltó algo.
        total_procesadas = len(entries) + descartadas_duplicado
        if total_abiertas_esperadas is not None:
            faltantes = total_abiertas_esperadas - total_procesadas
            if faltantes <= 0:
                print(f"✅ Verificación de completitud: se esperaban {total_abiertas_esperadas} licitaciones "
                      f"abiertas y se contabilizaron {total_procesadas} (nuevas + ya conocidas). Todo cuadra.")
            else:
                print(f"⚠️ Verificación de completitud: Ariba reporta {total_abiertas_esperadas} licitaciones "
                      f"abiertas, pero esta corrida solo contabilizó {total_procesadas} (nuevas + ya conocidas), "
                      f"incluso después de {numero_pasada} pasada(s) de scroll. Podrían faltar hasta {faltantes} "
                      f"licitaciones sin capturar — vuelve a correr el script para intentar capturarlas.")
        else:
            print("⚠️ No se pudo verificar completitud (no se leyó el total esperado de 'Estado: Abierto').")

        # Visitar cada link obtenido en paso anterior
        for entry in entries:
            try:
                folder_url = ""
                entry_description = ""
                product_quantity = ""
                archivos_descargados_nombres = []  # Para verificar después si el plano mencionado en el texto sí se descargó
                print("Visitando link: "+entry["url"])
                await page.goto(entry["url"], timeout=60000)
                await asyncio.sleep(10);
                try:
                    # BILINGÜE: la etiqueta puede venir en español ("Texto Largo:")
                    # o en inglés ("Long Text:" / "Long Description:") según el
                    # idioma configurado en Ariba.
                    _ETIQUETAS_TEXTO_LARGO = ["Texto Largo:", "Long Text:", "Long Description:", "Texto largo:"]
                    td_label = None
                    etiqueta_usada = None
                    for _etiqueta in _ETIQUETAS_TEXTO_LARGO:
                        _cand = page.locator("td", has_text=_etiqueta)
                        if await _cand.count() > 0:
                            td_label = _cand
                            etiqueta_usada = _etiqueta
                            break

                    if td_label is not None:
                        td_value = td_label.locator("xpath=following-sibling::td[1]")
                        entry_description = await td_value.text_content()
                        if entry_description:
                            entry_description = entry_description.strip()
                            print(f"Texto Largo encontrado ('{etiqueta_usada}'): {entry_description}")
                        else:
                            print(f"'{etiqueta_usada}' encontrado pero sin valor")
                    else:
                        print("No se encontró etiqueta de Texto Largo en esta página (ni en español ni en inglés)")
                except Exception as e:
                    print(f"Error al buscar el Texto Largo: {e}")

                # --- Respaldo: plantillas alternativas de Ariba (ej. compras
                # directas tipo "ECO") no usan la etiqueta "Texto Largo:". En su
                # lugar, la descripción vive en una celda con "colspan" (el ancho
                # exacto y el estilo varían entre licitaciones, así que en vez de
                # buscar un valor fijo, se toma la celda con colspan que tenga el
                # texto MÁS LARGO de la página — la descripción técnica siempre es
                # bastante más extensa que cualquier otro texto con colspan).
                # Se busca en TODOS los frames de la página (no solo el principal),
                # porque Ariba a veces mete este contenido dentro de un iframe.
                if not entry_description:
                    try:
                        mejor_texto = ""
                        candidatos_totales = 0
                        for fr in page.frames:
                            try:
                                candidatos = fr.locator('td[colspan]')
                                count_fr = await candidatos.count()
                            except Exception:
                                continue
                            candidatos_totales += count_fr
                            for idx in range(count_fr):
                                try:
                                    texto_alt = await candidatos.nth(idx).text_content()
                                except Exception:
                                    continue
                                if texto_alt:
                                    texto_alt = texto_alt.strip()
                                    if len(texto_alt) > len(mejor_texto):
                                        mejor_texto = texto_alt
                        print(f"🔍 Respaldo Texto Largo: {candidatos_totales} celda(s) con colspan revisadas en "
                              f"{len(page.frames)} frame(s); mejor candidato ({len(mejor_texto)} caracteres): "
                              f"{mejor_texto[:80]!r}")
                        # Rechazar texto que claramente NO es una descripción de
                        # producto, sino navegación/interfaz de Ariba. Esto pasa
                        # con licitaciones de servicios/proyectos (ej. servicios
                        # o proyectos), que no tienen ficha de producto
                        # y antes guardaban "< Volver al panel de instrucciones..."
                        # como si fuera la descripción.
                        _BASURA_NAVEGACION = (
                            # Español
                            "Volver al panel",
                            "Sincronizar archivos con el escritorio",
                            "Descargar tutoriales",
                            "Mensajes de evento",
                            "Equipo de respuesta",
                            # Inglés (por si la interfaz está en ese idioma)
                            "Go back to",
                            "Sync files with desktop",
                            "Download Tutorials",
                            "Event Messages",
                            "Response Team",
                        )
                        _es_navegacion = any(marca in mejor_texto for marca in _BASURA_NAVEGACION)

                        if len(mejor_texto) > 60 and not _es_navegacion:
                            entry_description = mejor_texto
                        elif _es_navegacion:
                            print("⚠️ Esta licitación no tiene ficha de producto (probablemente es de "
                                  "servicios/proyecto). Se deja el texto largo vacío en vez de guardar "
                                  "texto de navegación.")
                            print(f"Texto Largo (formato alternativo) encontrado: {entry_description}")
                    except Exception as e:
                        print(f"Error al buscar Texto Largo (formato alternativo): {e}")
                try:
                    td_quantity = page.locator("td", has_text="Less...")
                    td_quantity_es = page.locator("td", has_text="Menos...")
                    td_q = td_quantity if (await td_quantity.count()) > 0 else td_quantity_es
                    if td_q:
                        td_qvalue = td_q.locator("xpath=following-sibling::td[1]")
                        product_quantity = await td_qvalue.text_content()
                        if product_quantity:
                            product_quantity = product_quantity.strip()
                            print(f"Cantidad del producto: {product_quantity}")
                        else:
                            print("'Cantidad:' encontrada pero sin valor")
                    else:
                        print("No se encontró 'Less...' en esta página")
                except Exception as e:
                    print(f"Error al buscar 'Less...': {e}")
                links = await page.locator(SELECTORS["download_links"]).all()
                print(f"Links encontrados: {len(links)}")
                for element in links:
                  try:
                    await element.click()
                    await page.wait_for_selector(SELECTORS["link_popup"], timeout=30000)
                    popup = page.locator(SELECTORS["link_popup"])
                    # buscar items dentro del popup
                    menu_items = popup.locator("a[role=menuitem]")
                    await asyncio.sleep(1)
                    count = await menu_items.count()
                    if count >= 2:
                        last_text = await menu_items.nth(count - 1).text_content()
                        if last_text and ("Download all attachments" in last_text or "Descargar todos los adjuntos" in last_text):
                            print(f"Encontrado popup con archivos descargables: {count-1}")
                            for i in range(count - 1):
                                try:
                                    await element.click()
                                    await page.wait_for_selector(SELECTORS["link_popup"], timeout=10000)
                                    _popup = page.locator(SELECTORS["link_popup"])
                                    await asyncio.sleep(1)
                                    _menu_items = _popup.locator("a[role=menuitem]")
                                    raw_text = await _menu_items.nth(i).text_content()
                                    if not raw_text:
                                        continue
                                    filename = raw_text.strip()

                                    # Usar filtro antes de descargar
                                    if should_ignore(filename):
                                        print(f"⏩ Archivo ignorado por filtro: {filename}")
                                        continue
                                    async with page.expect_download(timeout=30000) as dl_info:
                                        await _menu_items.nth(i).click()
                                    download = await dl_info.value

                                    file_name = download.suggested_filename or f"{entry['id']}_{i}.dat"

                                    # Segunda verificación: el texto del menú (arriba)
                                    # a veces NO coincide con el nombre real del archivo
                                    # ya descargado (ej. el menú dice algo genérico pero
                                    # el archivo real se llama "INFORMACIÓN IMPORTANTE AL
                                    # PROVEEDOR.pdf"). Se revisa también el nombre real
                                    # antes de gastar tiempo subiéndolo a Drive.
                                    if should_ignore(file_name):
                                        print(f"⏩ Archivo ignorado por filtro (por nombre real tras descargar): {file_name}")
                                        continue

                                    saved_path = DOWNLOAD_TEMP / file_name

                                    await download.save_as(str(saved_path))
                                    print(f"Archivo descargado: {saved_path}")
                                    archivos_descargados_nombres.append(file_name)
                                    # Subir a Drive
                                    folder_id, folder_link = g.ensure_folder(entry["id"])
                                    folder_url = f"https://drive.google.com/drive/folders/{folder_id}"

                                    # Si es zip o rar, descomprimir y subir solo permitidos
                                    if saved_path.suffix.lower() in {'.zip', '.rar'}:
                                        process_archive(saved_path, folder_id, g)
                                    else:
                                        file_id, file_link = g.upload_file(saved_path, folder_id)
                                        print(f"Archivo subido a Drive: {file_link}")
                                    await asyncio.sleep(8)

                                except Exception as e:
                                    print(f"Error al descargar/subir archivo #{i}: {e}")
                        else:
                            print("No se encontró popup con archivos descargables")
                    else:
                        print(f"Se esperaban al menos 2 elementos en popup, encontrados {count}")
                  except Exception as e:
                    # Un hipo de red o un popup que no abre en ESTE adjunto ya no
                    # tira abajo todo el programa: se salta este link y se sigue
                    # con el resto de la licitación.
                    print(f"⚠️ Error procesando un link de descarga (se continúa con el siguiente): {e}")

                # --- Verificación: si el "Texto largo" menciona un número de
                # plano, ¿alguno de los archivos descargados coincide? ---
                # Heurística simple por palabras clave (sin IA): busca
                # "NUMERO PLANO:", "PLANO:", "N° PLANO:" seguido del código,
                # y avisa si NINGÚN archivo descargado contiene ese código en
                # su nombre — típico en productos mecánicos/estructurales,
                # donde el plano suele ser el archivo clave más importante.
                match_plano = re.search(
                    r'(?:N[ÚU]MERO\s+PLANO|N[°º]?\s*PLANO|PLANO)\s*:?\s*([A-Za-z0-9][A-Za-z0-9\-_./]{2,40})',
                    entry_description, re.IGNORECASE
                )
                if match_plano:
                    codigo_plano = match_plano.group(1).strip().rstrip('.,;')
                    # Normalizar para comparar sin importar mayúsculas/guiones
                    codigo_normalizado = re.sub(r'[^A-Za-z0-9]', '', codigo_plano).lower()
                    encontrado = any(
                        codigo_normalizado in re.sub(r'[^A-Za-z0-9]', '', nombre).lower()
                        for nombre in archivos_descargados_nombres
                    )
                    if not encontrado:
                        print(f"⚠️ PLANO MENCIONADO PERO NO DESCARGADO: el texto menciona el plano "
                              f"'{codigo_plano}', pero ningún archivo descargado ({archivos_descargados_nombres}) "
                              f"coincide con ese código. Revisar manualmente: {entry['url']}")

                g.insert_rows([[execution_date, "NO", entry["name"], entry["id"], entry["end_time"], entry_description, product_quantity, "Pendiente revisión", entry["url"], folder_url]])
                nuevas_filas += 1
            except Exception as e:
                # Si UNA licitación falla por completo (timeout de red,
                # página caída, etc.) no se pierde el resto del trabajo:
                # se registra el error y se sigue con la siguiente.
                print(f"❌ Error procesando la licitación {entry.get('id','?')} (se continúa con la siguiente): {e}")

                # Si el NAVEGADOR se cayó (no solo esta página), todas las
                # licitaciones siguientes fallarían en cascada. Se detecta y se
                # reabre el navegador para poder continuar con el resto.
                if "browser has been closed" in str(e) or "Target page, context or browser" in str(e):
                    print("🔄 El navegador se cerró inesperadamente. Reabriéndolo para continuar...")
                    try:
                        try:
                            await browser.close()
                        except Exception:
                            pass

                        browser = await p.chromium.launch(
                            headless=HEADLESS,
                            args=["--disable-blink-features=AutomationControlled", "--window-size=1920,1080"]
                        )
                        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
                        await context.route("**/s3.ariba.com/**", _forzar_descarga_imagenes)
                        await context.route("**/*.tif", _forzar_descarga_imagenes)
                        await context.route("**/*.tiff", _forzar_descarga_imagenes)
                        page = await context.new_page()

                        # Volver a iniciar sesión, porque el navegador nuevo no
                        # conserva la sesión del anterior.
                        print("🔑 Volviendo a iniciar sesión...")
                        await page.goto(ARIBA_LOGIN_URL, timeout=180000)
                        await page.wait_for_selector(SELECTORS["username"], timeout=120000)
                        await page.fill(SELECTORS["username"], ARIBA_EMAIL)
                        await page.wait_for_selector(SELECTORS["username_button"], timeout=60000)
                        await page.locator(SELECTORS["username_button"]).click()
                        await page.wait_for_selector(SELECTORS["password"], timeout=180000)
                        await page.fill(SELECTORS["password"], ARIBA_PASSWORD)
                        await asyncio.sleep(3)
                        await page.locator(SELECTORS["password_button"]).click()
                        await asyncio.sleep(15)
                        print("✅ Sesión restablecida, continuando con las licitaciones restantes.")
                    except Exception as e2:
                        print(f"❌ No se pudo reabrir el navegador: {e2}. Se detiene el procesamiento.")
                        break

        # Cerrar navegador
        end_time = time.time()
        elapsed = end_time - start_time
        minutes, seconds = divmod(int(elapsed), 60)
        print(f"\n✅ Proceso terminado. Nuevas filas creadas: {nuevas_filas}. Tiempo total: {elapsed:.1f} segundos.")


# -----------------------
# CLI
# -----------------------
def print_usage():
    print("""
Uso:
  python app.py bootstrap      # Ejecuta scraping, guarda en Google Sheet y sube docs a Drive

Configuración en .env obligatoria:
  ARIBA_LOGIN_URL, ARIBA_EMAIL, ARIBA_PASSWORD,
  GOOGLE_SERVICE_ACCOUNT_FILE, GOOGLE_SHEET_ID, GOOGLE_DRIVE_FOLDER_ID
""")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print_usage()
        raise SystemExit(1)
    cmd = sys.argv[1].lower()
    if cmd == "bootstrap":
        asyncio.run(scrap())
    else:
        print_usage()