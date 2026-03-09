# services/pdf_teachers.py
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

def generate_teachers_list_pdf(path: str, center_name: str, title: str, items):
    styles = getSampleStyleSheet()
    style_center_small = ParagraphStyle(name="CenterSmall", parent=styles["Normal"], alignment=TA_CENTER, fontSize=9)
    style_title        = ParagraphStyle(name="Title",       parent=styles["Heading1"], alignment=TA_CENTER, fontSize=16, leading=20)
    style_item         = ParagraphStyle(name="Item",        parent=styles["Normal"], fontSize=11, leading=14)

    doc = SimpleDocTemplate(path, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=16*mm, bottomMargin=16*mm)

    flow = []
    if center_name:
        flow.append(Paragraph(center_name, style_center_small))
    flow.append(Paragraph(title, style_title))
    flow.append(Spacer(1, 8))

    li = [ListItem(Paragraph(str(it), style_item)) for it in items]
    flow.append(ListFlowable(li, bulletType='1', start='1', leftIndent=12, bulletFontName="Helvetica"))

    doc.build(flow)
