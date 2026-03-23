import pandas as pd
from datetime import datetime
from sqlalchemy import text
import tempfile
import os

# ---------------------------------------------
# Tablas válidas que se pueden vaciar manualmente
# (users y alembic_version se incluyen en backup,
#  pero NO se vacían ni se importan)
# ---------------------------------------------
VALID_TABLES = {
    "schedule_slots": "Horarios",
    "absences": "Ausencias",
    "leaves": "Bajas",
    "school_calendar": "Calendario escolar",
    "teachers": "Profesorado",
}

EXPORT_TABLES = [
    "teachers",
    "schedule_slots",
    "absences",
    "leaves",
    "school_calendar",
    "users",
    "alembic_version",
]


# -----------------------------------------------------
# Exportar la copia de seguridad completa (8 hojas)
# -----------------------------------------------------
async def export_backup(db, admin_user):
    dfs = {}

    # INFO sheet
    counts = {}
    for table in EXPORT_TABLES:
        r = await db.execute(text(f"SELECT COUNT(*) FROM {table}"))
        counts[table] = r.scalar()

    info = pd.DataFrame([
        ["Fecha", datetime.now().isoformat()],
        ["Generado por", admin_user.email],
        ["---", "---"]
    ] + [[tbl, counts[tbl]] for tbl in EXPORT_TABLES], columns=["Clave", "Valor"])

    dfs["INFO"] = info

    # Each table → separate sheet
    for table in EXPORT_TABLES:
        result = await db.execute(text(f"SELECT * FROM {table} ORDER BY 1"))
        rows = result.mappings().all()
        df = pd.DataFrame(rows)
        dfs[table] = df

    # Save to temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
        with pd.ExcelWriter(tmp.name, engine="openpyxl") as writer:
            for sheet, df in dfs.items():
                df.to_excel(writer, sheet_name=sheet, index=False)

        return tmp.name


# -----------------------------------------------------
# Borrar SOLO las tablas seleccionadas
# -----------------------------------------------------
async def clear_tables(db, tables_to_clear):
    for table in tables_to_clear:
        if table in VALID_TABLES:
            await db.execute(text(f"DELETE FROM {table}"))
    await db.commit()


# -----------------------------------------------------
# Cargar copia desde Excel (sin tocar users ni alembic_version)
# -----------------------------------------------------
async def load_backup_from_excel(db, filepath):
    excel = pd.read_excel(filepath, sheet_name=None)

    # Orden recomendado para evitar claves foráneas
    order = ["teachers", "schedule_slots", "absences", "leaves", "school_calendar"]

    # Primero vaciar esas tablas
    for table in order:
        await db.execute(text(f"DELETE FROM {table}"))
    await db.commit()

    # Ahora importar
    for table in order:
        if table in excel:
            df = excel[table]
            if len(df) > 0:
                cols = ", ".join(df.columns)
                values = ", ".join([f":{c}" for c in df.columns])
                stmt = text(f"INSERT INTO {table} ({cols}) VALUES ({values})")
                for _, row in df.iterrows():
                    await db.execute(stmt, row.to_dict())

    await db.commit()
