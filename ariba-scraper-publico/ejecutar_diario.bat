@echo off
REM ============================================================
REM Ejecuta el ariba-scraper usando el venv del proyecto.
REM Pensado para ser llamado por el Programador de Tareas de Windows.
REM
REM OJO: en vez de "activar" el venv (call .venv\Scripts\activate.bat),
REM se llama DIRECTO al python.exe que vive dentro de .venv. Esto evita
REM problemas de PATH que aparecen cuando el Programador de Tareas
REM ejecuta el .bat con un entorno distinto al de una terminal normal
REM (causaba que terminara usando el Python del sistema, sin las
REM librerias instaladas, en vez del Python del venv).
REM ============================================================

REM Ir a la carpeta donde esta este .bat (la carpeta del proyecto)
cd /d "%~dp0"

REM Ruta directa al Python del venv (sin pasar por "activate")
set PYTHON_VENV=%~dp0.venv\Scripts\python.exe

REM Verificacion: si el Python del venv no existe en esa ruta, avisar
REM claramente en el log en vez de fallar en silencio con el Python
REM equivocado.
if not exist "%PYTHON_VENV%" (
    echo ERROR: no se encontro el Python del venv en "%PYTHON_VENV%"
    echo Verifica que la carpeta .venv exista junto a este archivo .bat,
    echo o vuelve a crearla con: python -m venv .venv
    exit /b 1
)

REM Forzar UTF-8 (respaldo extra, además del fix dentro de app.py) para
REM que los emojis en los mensajes no hagan fallar la escritura del log.
set PYTHONIOENCODING=utf-8

REM Correr el scraper y guardar un log con fecha en la carpeta "logs"
if not exist logs mkdir logs
set FECHA=%date:~-4%-%date:~3,2%-%date:~0,2%
set HORA=%time:~0,2%-%time:~3,2%
set HORA=%HORA: =0%

"%PYTHON_VENV%" app.py bootstrap >> "logs\ejecucion_%FECHA%_%HORA%.log" 2>&1

REM Descomenta la siguiente linea si quieres que la ventana se cierre sola
REM al terminar. Si la dejas comentada, la ventana queda abierta para que
REM puedas revisar si algo salio mal.
REM exit
