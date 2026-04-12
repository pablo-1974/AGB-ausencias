# services/pdf_teachers.py
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
import os

# 🔥 ORDENACIÓN SIN TILDES
from utils import normalize_name


def generate_teachers_list_pdf(
    path: str,
    center_name: str,
    title: str,
    items,
    date_str: str | None = None,
    logo_path: str = "static/logo.png"
):

    # ======================================================
    # ORDENACIÓN ALFABÉTICA ESPAÑOLA
    # ======================================================
    items = sorted(items, key=lambda it: normalize_name(it["name"]))

    styles = getSampleStyleSheet()

    style_center_small = ParagraphStyle(
        name="CenterSmall", parent=styles["Normal"],
        alignment=TA_CENTER, fontSize=9
    )
    style_title = ParagraphStyle(
        name="Title", parent=styles["Heading1"],
        alignment=TA_CENTER, fontSize=16, leading=20
    )
    style_subtle = ParagraphStyle(
        name="Subtle", parent=styles["Normal"],
        alignment=TA_CENTER, fontSize=9
    )
    style_item = ParagraphStyle(
        name="Item", parent=styles["Normal"],
        fontSize=11, leading=14
    )
    style_num = ParagraphStyle(
        name="Num", parent=styles["Normal"],
        fontSize=11, leading=14, alignment=2  # derecha
    )
    style_email = ParagraphStyle(
        name="Email", parent=styles["Normal"],
        fontSize=10, leading=13
    )

    doc = SimpleDocTemplate(
        path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm
    )

    flow = []

    # ============================
    #  CABECERO CON LOGO A LA IZQUIERDA
    # ============================
    
    logo = None
    if logo_path and os.path.exists(logo_path):
        logo = Image(
            logo_path,
            width=22 * mm,
            height=22 * mm
        )
    
    text_block = []
    
    if center_name:
        text_block.append(Paragraph(center_name, style_center_small))
    
    text_block.append(Paragraph(title, style_title))
    
    if date_str:
        text_block.append(Paragraph(date_str, style_subtle))
    
    header = Table(
        [[logo, text_block]],
        colWidths=[26 * mm, None]
    )
    
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (0, 0), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    
    flow.append(header)
    flow.append(Spacer(1, 10))

    # ============================
    #   TABLA DE PROFESORADO
    # ============================
    data = []

    if items:
        for idx, it in enumerate(items, start=1):
            num_par = Paragraph(f"{idx}.", style_num)
            name_par = Paragraph(it["name"], style_item)
            email_par = Paragraph(it["email"], style_email)
            data.append([num_par, name_par, email_par])
    else:
        data.append([
            Paragraph("", style_num),
            Paragraph("— Sin datos —", style_item),
            Paragraph("", style_email)
        ])

    # ============================
    #   DIMENSIONES Y ESTILO TABLA
    # ============================
    page_w, _ = A4
    usable_w = page_w - (doc.leftMargin + doc.rightMargin)

    num_w = 14 * mm            # columna números
    name_w = usable_w * 0.60   # nombres
    email_w = usable_w * 0.40  # emails

    table = Table(data, colWidths=[num_w, name_w, email_w])

    table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),

        ('LEFTPADDING', (0, 0), (0, -1), 0),
        ('RIGHTPADDING', (0, 0), (0, -1), 0),

        ('LEFTPADDING', (1, 0), (1, -1), 6),

        ('LEFTPADDING', (2, 0), (2, -1), 2),
        ('RIGHTPADDING', (2, 0), (2, -1), 2),

        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
    ]))

    flow.append(table)
    doc.build(flow)
