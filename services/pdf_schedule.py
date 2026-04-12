# services/pdf_schedule.py
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
from reportlab.lib.units import mm

import os
from config import settings

def generate_schedule_pdf(path, teacher_name, center_name, schedule):
    """
    schedule = matriz 7x5 de dicts (CLASE / GUARDIA) o None
    """

    styles = getSampleStyleSheet()
    style_center = ParagraphStyle(
        name="Center",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
    )
    style_title = ParagraphStyle(
        name="Title",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontSize=16,
        leading=20,
    )
    style_center_small = ParagraphStyle(
        name="CenterSmall",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
    )
    style_cell = ParagraphStyle(
        name="Cell",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=8,
        leading=9,
    )

    days = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
    franjas = ["1ª", "2ª", "3ª", "Recreo", "4ª", "5ª", "6ª"]

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=12*mm, bottomMargin=12*mm
    )

    flow = []

    # ---------------------------------
    # Encabezado con logo + textos
    # ---------------------------------
    
    logo = None
    if settings.LOGO_PATH and os.path.exists(settings.LOGO_PATH):
        logo = Image(
            settings.LOGO_PATH,
            width=22 * mm,
            height=22 * mm
        )
    
    text_block = [
        Paragraph(center_name, style_center_small),
        Spacer(1, 2),
        Paragraph(f"Horario semanal — {teacher_name}", style_title),
    ]
    
    header_table = Table(
        [[logo, text_block]],
        colWidths=[26 * mm, None]
    )
    
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    
    flow.append(header_table)
    flow.append(Spacer(1, 6))

    # --------------------------
    # Construcción de la tabla
    # --------------------------

    # Primera fila (cabecera)
    data = [["Hora"] + days]

    # Primera columna será más estrecha
    page_width, page_height = A4
    usable = page_width - (doc.leftMargin + doc.rightMargin)

    # Columna 0 más estrecha
    col0 = 18 * mm
    col_other = (usable - col0) / 5

    col_widths = [col0] + [col_other] * 5

    # Alturas
    row_heights = [12*mm]  # cabecera
    for fr in franjas:
        if fr == "Recreo":
            row_heights.append(9*mm)
        else:
            row_heights.append(17*mm)

    # Rellenar datos
    for i, fr in enumerate(franjas):
        row = [fr]
        for d in range(5):
            item = schedule[i][d]
            if item is None:
                row.append("")
            elif item["type"] == "CLASS":
                txt = f"{item['group']}<br/>{item['room']}<br/>{item['subject']}"
                row.append(Paragraph(txt, style_cell))
            elif item["type"] == "GUARD":
                txt = f"{item['guard_type']}"
                row.append(Paragraph(txt, style_cell))
            else:
                row.append("")
        data.append(row)

    # Convertir cabeceras a Paragraph
    data[0] = [Paragraph(str(x), style_center) for x in data[0]]

    # Convertir etiquetas de franja a Paragraph
    for i in range(1, len(data)):
        data[i][0] = Paragraph(str(data[i][0]), style_center)

    table = Table(data, colWidths=col_widths, rowHeights=row_heights, repeatRows=1)

    # Estilos
    table_style = TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.grey),
        ("ALIGN", (0,0), (-1,0), "CENTER"),
        ("BACKGROUND", (0,0), (-1,0), colors.lightgrey),  # primera fila
        ("BACKGROUND", (0,1), (0,-1), colors.lightgrey),  # primera columna (Franja)
        ("VALIGN", (0,0), (-1,-1), "MIDDLE"),
    ])

    # Recreo sombreado
    recreo_row = 1 + franjas.index("Recreo")
    table_style.add("BACKGROUND", (0, recreo_row), (-1, recreo_row), colors.whitesmoke)
    table_style.add("TEXTCOLOR", (1, recreo_row), (-1, recreo_row), colors.darkmagenta)

    table.setStyle(table_style)

    flow.append(table)
    flow.append(Spacer(1, 8))
    flow.append(Paragraph("Generado automáticamente", styles["Italic"]))

    doc.build(flow)
