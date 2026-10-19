[README_scraper.md](https://github.com/user-attachments/files/33671579/README_scraper.md)

Scraper en Python que monitorea un portal de compras públicas, detecta licitaciones nuevas y **verifica que no se pierda ninguna** antes de guardar los resultados.

> Los datos de los ejemplos son ficticios. Este repositorio no incluye credenciales ni información real de ningún cliente.

## El problema

Los portales de licitaciones cargan sus listados con scroll virtual: solo se renderizan las filas visibles, así que un scraper simple se salta registros sin avisar. Además, revisar a mano cada día cuáles licitaciones son nuevas y relevantes toma tiempo y es fácil que algo se escape.

## Qué hace

- **Scroll virtual con múltiples pasadas**: recorre el listado completo varias veces para no dejar filas fuera.
- **Verificación de completitud**: compara lo extraído con el total que informa el portal y avisa si faltan registros.
- **Revisión por licitación**: revisa los documentos y datos asociados a cada una.
- **Manejo de archivos adjuntos**: soporta distintos formatos de descarga, incluido RAR.
- **Integración con Google Sheets y Drive**: guarda los resultados en una planilla con llamadas por lotes para reducir el uso de la API.
- **Ejecución programada**: se lanza automáticamente con un archivo `.bat` y el Programador de tareas de Windows.

## Tecnologías

Python · Playwright · Google Sheets API · Google Drive API · Windows Task Scheduler

## Instalación

```bash
git clone https://github.com/[tu-usuario]/[nombre-del-repo].git
cd [nombre-del-repo]
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
```

## Configuración

1. Copia `.env.example` a `.env` y completa tus valores:

```
SPREADSHEET_ID=tu_id_de_planilla
DRIVE_FOLDER_ID=tu_id_de_carpeta
GOOGLE_CREDENTIALS_FILE=credentials.json
PORTAL_URL=https://[portal-de-licitaciones]
```

2. Coloca tu archivo `credentials.json` de Google en la raíz del proyecto (no se sube al repositorio).

## Uso

```bash
python app.py
```

### Ejecución programada (Windows)

1. Ajusta las rutas en `ejecutar.bat`.
2. Crea una tarea en el Programador de tareas que ejecute ese archivo con la frecuencia que necesites.

## Ejemplo de resultado

| ID | Título | Organismo | Cierre | Estado |
|----|--------|-----------|--------|--------|
| 1001 | Suministro de equipos de ejemplo | Empresa Demo S.A. | 2026-11-05 | Nueva |
| 1002 | Servicio de mantención ficticio | Organismo Ejemplo | 2026-11-12 | Nueva |
| 1003 | Compra de insumos de prueba | Entidad Muestra | 2026-11-20 | Sin cambios |

```
[INFO] Pasada 1/3: 48 filas
[INFO] Pasada 2/3: 63 filas
[INFO] Pasada 3/3: 67 filas
[OK]   Verificación de completitud: 67/67 licitaciones extraídas
```

## Estructura del proyecto

```
.
├── app.py
├── requirements.txt
├── ejecutar.bat
├── .env.example
├── .gitignore
└── README.md
```

## Autor

Alberto — Técnico Analista Programador. Automatización y extracción de datos con Python y Google Apps Script.
Contacto: [tu-correo]
