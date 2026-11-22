import fitz
import docx
from pptx import Presentation

from app.config.settings import get_settings
from app.ingestion.cleaner import clean_pages, clean_text
from app.ingestion.loader import ExtractionError, Page, load_document
import pytest


def test_pdf_page_numbers(tmp_path):
    p = tmp_path / "a.pdf"
    d = fitz.open()
    for i in range(3):
        d.new_page().insert_text((72, 72), f"Content of page {i + 1} " * 10)
    d.save(p)
    pages = load_document(p, "pdf")
    assert [x.page_number for x in pages] == [1, 2, 3]
    assert "page 2" in pages[1].text


def test_scanned_pdf_uses_ocr(tmp_path):
    import shutil
    if not shutil.which("tesseract"):
        pytest.skip("tesseract not installed")
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (1200, 400), "white")
    dr = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 60)
    except OSError:
        pytest.skip("no font")
    dr.text((40, 150), "ATTENDANCE SEVENTY FIVE", fill="black", font=font)
    png = tmp_path / "s.png"
    img.save(png)
    d = fitz.open()
    pg = d.new_page(width=600, height=200)
    pg.insert_image(pg.rect, filename=str(png))
    pdf = tmp_path / "scan.pdf"
    d.save(pdf)
    pages = load_document(pdf, "pdf")
    assert pages[0].ocr is True and "ATTENDANCE" in pages[0].text.upper()


def test_docx_headings_and_tables(tmp_path):
    p = tmp_path / "a.docx"
    d = docx.Document()
    d.add_heading("Attendance Policy", 1)
    d.add_paragraph("75 percent required.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Course", "Credits"
    d.save(p)
    pages = load_document(p, "docx")
    assert pages[0].page_number is None          # never invented
    assert "# Attendance Policy" in pages[0].text and "Course | Credits" in pages[0].text


def test_pptx_slide_numbers_and_notes(tmp_path):
    p = tmp_path / "a.pptx"
    prs = Presentation()
    for i in (1, 2):
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = f"Slide {i}"
        s.placeholders[1].text = "body"
        s.notes_slide.notes_text_frame.text = f"note{i}"
    prs.save(p)
    pages = load_document(p, "pptx")
    assert [x.page_number for x in pages] == [1, 2] and "note2" in pages[1].text


def test_csv_header_pairing(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("Company,CTC\nAcme,12 LPA\nBeta,9 LPA\n", encoding="utf-8")
    pages = load_document(p, "csv")
    assert "Company: Acme; CTC: 12 LPA" in pages[0].text


def test_txt_encodings(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes("café notice".encode("cp1252"))
    assert "caf" in load_document(p, "txt")[0].text


def test_empty_and_corrupt_raise(tmp_path):
    e = tmp_path / "e.txt"
    e.write_text("   ")
    with pytest.raises(ExtractionError):
        load_document(e, "txt")
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(ExtractionError):
        load_document(bad, "pdf")


def test_clean_text():
    assert clean_text("exam-\nination  rules\x00") == "examination rules"
    assert clean_text("a\n\n\n\n\nb") == "a\n\nb"
    assert clean_text("text\nPage 3 of 10\nmore") == "text\nmore"


def test_repeated_headers_removed():
    pages = [Page(f"NIT RAIPUR HEADER\nunique body {i}\n", i + 1) for i in range(6)]
    out = clean_pages(pages)
    assert all("HEADER" not in p.text for p in out) and "unique body 3" in out[3].text
