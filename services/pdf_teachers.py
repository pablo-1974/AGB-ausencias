# services/pdf_teachers.py
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors

def generate_teachers_list_pdf(path: str, center_name: str, title: str, items, date_str: str | None = None):
    """
    Genera un PDF con lista numerada:
      - Numeración: '1.' (punto) + ESPACIO visual (padding en la segunda columna).
      - Alineación: puntos alineados en columna fija; primera letra alineada.
      - Si date_str está presente, se muestra bajo el título (p.ej. para 'Profesorado Actual').
    """
    styles = getSampleStyleSheet()
    style_center_small = ParagraphStyle(
        name="CenterSmall", parent=styles["Normal"], alignment=TA_CENTER, fontSize=9
    )
    style_title = ParagraphStyle(
        name="Title", parent=styles["Heading1"], alignment=TA_CENTER, fontSize=16, leading=20
    )
    style_subtle = ParagraphStyle(
        name="Subtle", parent=styles["Normal"], alignment=TA_CENTER, fontSize=9
    )
    style_item = ParagraphStyle(
        name="Item", parent=styles["Normal"], fontSize=11, leading=14  # texto de nombre
    )
    style_num = ParagraphStyle(
        name="Num", parent=styles["Normal"], fontSize=11, leading=14, alignment=2  # 2 = TA_RIGHT
    )

    # Documento
    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=16*mm, bottomMargin=16*mm
    )

    flow = []
    if center_name:
        flow.append(Paragraph(center_name, style_center_small))
    flow.append(Paragraph(title, style_title))

    # Línea con fecha cuando nos la pasan (p.ej. para "Profesorado Actual")
    if date_str:
        flow.append(Paragraph(date_str, style_subtle))

    flow.append(Spacer(1, 8))

    # --- Tabla 2 columnas: [nº.] [Nombre] ---
    # Columna 0: "1." , "2." ... alineada a la derecha (punto alineado verticalmente)
    # Columna 1: "Nombre Apellidos" alineado a la izquierda
    data = []
    if items and len(items) > 0:
        for idx, it in enumerate(items, start=1):
            # Número + punto (el "espacio" se logra con el padding de la 2ª columna para alineación perfecta)
            num_par = Paragraph(f"{idx}.", style_num)
            txt_par = Paragraph(str(it), style_item)
            data.append([num_par, txt_par])
    else:
        data.append([Paragraph("", style_num),
                     Paragraph("— Sin datos —", style_item)])

    # Anchos: números fijos (14 mm), texto = resto
    page_w, _ = A4
    usable_w = page_w - (doc.leftMargin + doc.rightMargin)
    num_w = 14 * mm
    text_w = usable_w - num_w

    table = Table(data, colWidths=[num_w, text_w])

    # Estilos:
    # - RIGHTPADDING en col 0 = 0 (ajusta pegado al borde)
    # - LEFTPADDING en col 1 = 4-6 pt aprox. para "simular" el espacio tras el punto
    table_style = TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING',  (0, 0), (0, -1), 0),   # col 0
        ('RIGHTPADDING', (0, 0), (0, -1), 0),
        ('LEFTPADDING',  (1, 0), (1, -1), 6),   # col 1 => 'espacio' tras el punto
        ('RIGHTPADDING', (1, 0), (1, -1), 0),
        ('TOPPADDING',   (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 1),
        # Si alguna vez quieres líneas guía:
        # ('GRID', (0,0), (-1,-1), 0.25, colors.lightgrey),
    ])
    table.setStyle(table_style)

    flow.append(table)
    doc.build(flow)
