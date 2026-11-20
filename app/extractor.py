import io
from typing import Tuple
import pypdf
import docx

def extract_text_from_file(filename: str, content: bytes) -> Tuple[str, str]:
    """
    Extracts text and a suggested topic from uploaded PDF, DOCX, or TXT file.
    Returns: (extracted_text, suggested_topic)
    """
    ext = filename.lower().split('.')[-1]
    suggested_topic = filename.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ').title()
    text = ""

    if ext == "pdf":
        reader = pypdf.PdfReader(io.BytesIO(content))
        pages_text = []
        for page in reader.pages:
            t = page.extract_text()
            if t:
                pages_text.append(t)
        text = "\n\n".join(pages_text)

    elif ext in ["docx", "doc"]:
        doc = docx.Document(io.BytesIO(content))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        text = "\n\n".join(paragraphs)

    elif ext in ["txt", "md"]:
        for encoding in ["utf-8", "utf-8-sig", "cp1251", "latin-1"]:
            try:
                text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    else:
        raise ValueError(f"Непідтримуваний формат файлу: .{ext}. Підтримуються: .pdf, .docx, .txt, .md")

    text = text.strip()
    if not text:
        raise ValueError(f"Файл '{filename}' порожній або з нього не вдалося вилучити текстовий шар.")

    return text, suggested_topic
