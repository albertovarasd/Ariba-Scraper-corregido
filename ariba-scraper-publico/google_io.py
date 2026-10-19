from typing import List, Any, Tuple
import gspread
from gspread.utils import rowcol_to_a1
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from pathlib import Path
from googleapiclient.http import MediaFileUpload

class GoogleIo:
    def __init__(self, service_account_file: str, sheet_id: str, root_folder_id: str):
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        self.creds = Credentials.from_service_account_file(service_account_file, scopes=scopes)
        self.gc = gspread.authorize(self.creds)
        self.sheet = self.gc.open_by_key(sheet_id).sheet1
        self.drive = build("drive", "v3", credentials=self.creds)
        self.root_folder_id = root_folder_id
        # Verificación temprana: confirma que la carpeta raíz de Drive existe y
        # es accesible con estas credenciales, para fallar acá con un error
        # claro en vez de más adelante en medio del scraping.
        self.drive.files().get(fileId=self.root_folder_id, fields="id, name", supportsAllDrives=True).execute()

    def get_last_filled_row(self) -> int:
        """
        Devuelve el índice (1-based) de la última fila con datos en la hoja completa.
        Si la hoja está vacía, devuelve 0.

        OPTIMIZADO: antes usaba get_all_values(), que descarga TODA la hoja
        (1500+ filas × 18 columnas) solo para contar filas. Ahora pide una
        sola columna, que es mucho más liviano y rápido.
        """
        values = self.sheet.col_values(1)
        return len(values)

    def ensure_capacity(self, target_row: int):
        """Si hace falta, agrega filas vacías para alcanzar 'target_row'."""
        current = self.sheet.row_count
        if target_row > current:
            self.sheet.add_rows(target_row - current)

    def insert_rows(self, data: List[List[Any]], start_col: int = 1):
        """
        Inserta arrays simples de datos fila por fila,
        justo después de la última fila con datos.
        """
        print(f"Insertando {len(data)} registros nuevos")
        last_row = self.get_last_filled_row()
        next_row = last_row + 1 if last_row > 0 else 1
        print(f"Última fila con datos = {last_row} → insertando desde fila {next_row}")

        # Asegurar capacidad UNA sola vez para todo el bloque, en vez de
        # consultar/ampliar la hoja en cada fila del bucle.
        self.ensure_capacity(next_row + len(data) - 1)

        for row_vals in data:
            end_col = start_col + len(row_vals) - 1
            start_a1 = rowcol_to_a1(next_row, start_col)
            end_a1 = rowcol_to_a1(next_row, end_col)
            a1_range = f"{start_a1}:{end_a1}"

            self.sheet.update(a1_range, [row_vals], value_input_option="USER_ENTERED")

            print(f"insertada fila {next_row}: {row_vals}")
            next_row += 1

    def ensure_folder(self, name: str) -> Tuple[str, str]:
        """
        Asegura que exista una carpeta con 'name' dentro del folder raíz.
        Devuelve (folder_id, webViewLink).
        Soporta tanto carpetas normales como unidades compartidas.
        """
        query = (
            f"mimeType='application/vnd.google-apps.folder' "
            f"and name='{name}' "
            f"and '{self.root_folder_id}' in parents "
            f"and trashed=false"
        )
        resp = self.drive.files().list(
            q=query,
            fields="files(id, name, webViewLink)",
            supportsAllDrives=True,
            includeItemsFromAllDrives=True
        ).execute()
        files = resp.get("files", [])

        if files:
            f = files[0]
            print(f"Carpeta ya existe: {f['name']} ({f['id']})")
            return f["id"], f["webViewLink"]

        # Crear carpeta nueva
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [self.root_folder_id],
        }
        folder = self.drive.files().create(
            body=metadata,
            fields="id, webViewLink",
            supportsAllDrives=True
        ).execute()
        print(f"Carpeta creada: {name} ({folder['id']})")
        return folder["id"], folder["webViewLink"]


    def upload_file(self, local_path: Path, parent_id: str) -> Tuple[str, str]:
        """
        Sube un archivo a Google Drive dentro de la carpeta con id = parent_id.
        Soporta también unidades compartidas.
        Devuelve (file_id, webViewLink).
        """
        file_metadata = {"name": local_path.name, "parents": [parent_id]}
        media = MediaFileUpload(str(local_path), resumable=True)
        up = self.drive.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True
        ).execute()

        print(f"Archivo subido a Drive: {local_path.name} ({up['id']})")
        return up["id"], up["webViewLink"]
