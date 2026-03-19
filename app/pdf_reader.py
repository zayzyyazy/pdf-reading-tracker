from pypdf import PdfReader
from docx import Document


def extract_text_from_pdf(path):
    reader = PdfReader(path)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text


def extract_text_from_txt(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def extract_text_from_docx(path):
    doc = Document(path)
    text = ""
    for paragraph in doc.paragraphs:
        text += paragraph.text + "\n"
    return text
