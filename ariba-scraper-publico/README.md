# Ariba Tender Monitor

Scraper en Python que revisa cada día el portal de proveedores de **SAP Ariba**, detecta licitaciones nuevas de un comprador, **verifica que no se haya escapado ninguna**, descarga sus documentos a Google Drive y deja un registro en Google Sheets.

> Proyecto independiente, sin relación con SAP ni con ningún comprador. Úsalo solo con tu propia cuenta de proveedor y respetando los términos de uso del portal. Los ejemplos de este repositorio son ficticios.

## El problema

Las licitaciones se publican en un listado con **scroll virtual**: Ariba solo dibuja en pantalla las filas visibles y rellena sus celdas con AJAX. Un scraper simple se salta filas sin avisar, y revisar el listado a mano todos los días es lento y propenso a errores.

## Qué hace

1. Inicia sesión en Ariba (maneja el banner de cookies y los popups) y selecciona el comprador configurado.
2. Expande el grupo **Estado: Abierto** y lee cuántas licitaciones dice Ariba que hay.
3. Recorre el listado con **scroll virtual en hasta 5 pasadas**, reintentando las filas que aún no terminaron de cargar y descartando filas basura de la interfaz.
4. **Verificación de completitud:** compara lo contabilizado (nuevas + ya conocidas) con el total que informa Ariba y avisa si podrían faltar licitaciones.
5. Evita duplicados comparando contra la planilla, por ID y por URL.
6. Entra a cada licitación nueva y extrae título, ID, fecha de cierre, texto largo y cantidad (con respaldo para plantillas alternativas).
7. Descarga los adjuntos que pasan el filtro, crea una carpeta por licitación en Drive y sube los archivos. Si llega un `.zip` o `.rar`, lo descomprime y sube solo los formatos permitidos (`.pdf`, `.dwg`, `.xls(x)`, `.ppt(x)`); si no se puede descomprimir, sube el archivo comprimido para no perder el adjunto.
8. Avisa si el texto de la licitación menciona un número de plano que no aparece entre los archivos descargados.
9. Agrega una fila por licitación en Google Sheets, con el enlace a su carpeta de Drive.

Más detalles de robustez: funciona con la interfaz de Ariba en español o en inglés, fuerza la descarga de imágenes (`.tif`) que el navegador intentaría mostrar, reabre el navegador y vuelve a iniciar sesión si este se cae, y una licitación que falla no detiene a las demás.

## Tecnologías

Python · Playwright · Google Sheets API (gspread) · Google Drive API · rarfile · Programador de tareas de Windows

## Requisitos

- Python 3.10 o superior
- Una cuenta de proveedor en SAP Ariba con acceso al comprador que quieres monitorear
- Una cuenta de servicio de Google Cloud con las APIs de Sheets y Drive habilitadas. Comparte la planilla y la carpeta de Drive con el correo de esa cuenta de servicio.
- Opcional, para descomprimir `.rar`: la versión de **consola** de `UnRAR.exe` junto a `app.py`, o `unrar` disponible en el PATH. Sin ella, los `.rar` se suben sin descomprimir.

## Instalación

```bash
git clone https://github.com/<tu-usuario>/<nombre-del-repo>.git
cd <nombre-del-repo>
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Configuración

1. Copia `.env.example` a `.env` y completa los valores (la lista completa de variables está en ese archivo).
2. Guarda el JSON de tu cuenta de servicio en la ruta que pusiste en `GOOGLE_SERVICE_ACCOUNT_FILE`. Está en `.gitignore`, no se sube.
3. Opcional: copia `ignored_files.example.txt` como `ignored_files.txt` y agrega los patrones de los adjuntos que no quieres descargar (por ejemplo, formularios que se repiten en todas las licitaciones).

La planilla se llena a partir de la primera fila libre, con estas columnas:

| A | B | C | D | E | F | G | H | I | J |
|---|---|---|---|---|---|---|---|---|---|
| Fecha de ejecución | Marca inicial `NO` | Título | ID | Cierre | Texto largo | Cantidad | Estado | Enlace | Carpeta en Drive |

## Uso

```bash
python app.py bootstrap
```

Con `HEADLESS=false` en el `.env` se ve el navegador mientras trabaja, útil para depurar.

### Ejecución programada en Windows

`ejecutar_diario.bat` usa el Python del entorno `.venv` y guarda un log con fecha en `logs/`. Crea una tarea en el Programador de tareas de Windows que ejecute ese archivo con la frecuencia que necesites.

## Ejemplo de salida

Datos ficticios, con el formato real de los mensajes:

```
Iniciando extracción de filas con scroll virtual...

🔁 Pasada de scroll #1 de 5...
✅ Nuevo registro detectado: Doc1000000001
✅ Nuevo registro detectado: Doc1000000002
✅ Fin del scroll detectado (contenedor al final).
   (pasada #1: ya se cubrieron las 67 licitaciones esperadas, no hace falta seguir)
Total de registros nuevos: 2
✅ Verificación de completitud: se esperaban 67 licitaciones abiertas y se contabilizaron 67 (nuevas + ya conocidas). Todo cuadra.
Archivo subido a Drive: Especificaciones tecnicas.pdf (1AbC...)
✅ Proceso terminado. Nuevas filas creadas: 2. Tiempo total: 94.3 segundos.
```

Fila resultante en la planilla:

| Fecha | | Título | ID | Cierre | Texto largo | Cantidad | Estado | Enlace | Carpeta |
|---|---|---|---|---|---|---|---|---|---|
| 19/10/26 | NO | Suministro de equipos de ejemplo | Doc1000000001 | 05/11/2026 | Descripción técnica de ejemplo... | 10 UN | Pendiente revisión | https://… | https://drive.google.com/drive/folders/… |

## Pruebas

```bash
python -m unittest
```

## Estructura

```
.
├── app.py                      # flujo principal: login, scroll virtual, descargas, Sheets/Drive
├── dom_selectors.py            # selectores CSS de Ariba (si cambia su interfaz, se ajustan aquí)
├── file_filter.py              # filtro de adjuntos que no se descargan
├── google_io.py                # acceso a Google Sheets y Drive
├── test_file_filter.py
├── ejecutar_diario.bat         # lanzador para el Programador de tareas
├── requirements.txt
├── .env.example
└── ignored_files.example.txt
```

## Limitaciones

- Ariba cambia su interfaz de vez en cuando. Si el script se detiene en un paso, lo más probable es que haya que actualizar un selector en `dom_selectors.py`. Cuando falla el menú superior, guarda una captura `login_debug.png`.
- Si Ariba informa más licitaciones de las que el script logra contabilizar, lo avisa al final de la corrida. Volver a ejecutarlo suele capturar las que faltaron.

## Autor

Alberto Varas — Técnico Analista Programador. Automatización y extracción de datos con Python y Google Apps Script.
