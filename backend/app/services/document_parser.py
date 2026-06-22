import os
from pathlib import Path

def parse_document(file_path: str) -> str:
    """Extract full text from .docx / .doc / .pdf / .wps. Returns plain text."""
    ext = Path(file_path).suffix.lower()

    if ext == ".docx":
        return _parse_docx(file_path)
    elif ext == ".doc":
        return _parse_doc(file_path)
    elif ext == ".pdf":
        return _parse_pdf(file_path)
    elif ext == ".wps":
        return _parse_docx(file_path) if _is_docx(file_path) else _parse_doc(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

def _parse_docx(file_path: str) -> str:
    from docx import Document
    doc = Document(file_path)
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            parts.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            row_text = " | ".join(cell.text for cell in row.cells)
            if row_text.strip():
                parts.append(row_text)
    return "\n".join(parts)

def _parse_doc(file_path: str) -> str:
    try:
        from unstructured.partition.doc import partition_doc
        elements = partition_doc(filename=file_path)
        return "\n".join(str(el) for el in elements if str(el).strip())
    except Exception:
        try:
            return _parse_docx(file_path)
        except Exception:
            raise RuntimeError(f"Cannot parse .doc file: {file_path}")

def _parse_pdf(file_path: str) -> str:
    import pdfplumber
    parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                parts.append(text)
            for table in page.extract_tables():
                if table:
                    for row in table:
                        row_text = " | ".join(str(c or "") for c in row)
                        if row_text.strip():
                            parts.append(row_text)
    return "\n".join(parts)

def _is_docx(file_path: str) -> bool:
    """Check if file is actually a ZIP-based docx (WPS files sometimes are)."""
    try:
        import zipfile
        with zipfile.ZipFile(file_path, 'r') as zf:
            return any('word/document.xml' in f for f in zf.namelist())
    except Exception:
        return False
