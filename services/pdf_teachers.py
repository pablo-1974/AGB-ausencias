from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
)
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER
from reportlab.lib import colors
import os


def generate_teachers_list_pdf(
    path: str,
    center_name: str,
    title: str,
    items,
    date_str: str | None = None,
    logo_path: str = "static/logo.png"   # NUEVO: logo
):

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
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=16*mm, bottomMargin=16*mm
    )

    flow = []

    # ============================
    #  CABECERO CON LOGO + TITULO
    # ============================
    if os.path.exists(logo_path):
        logo = Image(logo_path, width=22*mm, height=22*mm)
        logo.hAlign = "CENTER"
        flow.append(logo)
        flow.append(Spacer(1, 4))

    if center_name:
        flow.append(Paragraph(center_name, style_center_small))

    flow.append(Paragraph(title, style_title))

    if date_str:
        flow.append(Paragraph(date_str, style_subtle))

    flow.append(Spacer(1, 10))

    # ============================
    # TABLA (Nº) (NOMBRE) (EMAIL)
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

    # Ancho total
    page_w, _ = A4
    usable_w = page_w - (doc.leftMargin + doc.rightMargin)

    num_w = 14 * mm      # nº fijo
    email_w = 45 * mm    # email fijo
    name_w = usable_w - num_w - email_w

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
