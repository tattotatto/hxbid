import os
from pathlib import Path

def parse_document(file_path: str) -> str:
    """Extract full text from .docx / .doc / .pdf / .wps. Returns plain text."""
    ext = Path(file_path).suffix.lower()

    if ext in (".docx", ".doc", ".wps"):
        return _parse_docx_or_doc(file_path)
    elif ext == ".pdf":
        return _parse_pdf(file_path)
    else:
        raise ValueError(f"Unsupported file format: {ext}")

def _parse_docx_or_doc(file_path: str) -> str:
    """Try docx first, fall back to text extraction for legacy .doc."""
    # Try python-docx first (works for .docx and many .doc/.wps that are really docx)
    try:
        return _parse_docx(file_path)
    except Exception:
        pass

    # Try unstructured for legacy .doc
    try:
        from unstructured.partition.doc import partition_doc
        elements = partition_doc(filename=file_path)
        text = "\n".join(str(el) for el in elements if str(el).strip())
        if text.strip():
            return text
    except Exception:
        pass

    # Last resort: try antiword or textract
    try:
        import subprocess
        result = subprocess.run(["antiword", file_path], capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except Exception:
        pass

    raise RuntimeError(f"Cannot parse document: {file_path}. Please convert to .docx format.")

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
