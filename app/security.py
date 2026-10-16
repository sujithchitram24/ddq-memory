"""Authentication (LOCAL DEMO GRADE) and upload validation.

Auth here: an API key in the X-API-Key header maps to one organisation. We store only a
SHA-256 hash of each key. This is a clearly bounded mock: production would use an
identity provider (OIDC/JWT), per-user identities, key rotation and rate limits.
"""
import hashlib
import io
import secrets
import zipfile

from app.errors import AppError

ALLOWED = {
    "pdf": {"magic": b"%PDF-", "zip_marker": None},
    "docx": {"magic": b"PK\x03\x04", "zip_marker": "word/document.xml"},
    "xlsx": {"magic": b"PK\x03\x04", "zip_marker": "xl/workbook.xml"},
}


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_api_key() -> str:
    return "ddq_" + secrets.token_urlsafe(24)


def safe_display_name(filename: str | None) -> str:
    name = (filename or "upload").replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable())[:200]
    return name or "upload"


def detect_format(filename: str, data: bytes, max_unzipped: int) -> str:
    """Extension must be allowed AND the bytes must match it. Never trust either alone."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED:
        raise AppError(415, "UNSUPPORTED_TYPE", "Only .pdf, .docx and .xlsx files are accepted")
    spec = ALLOWED[ext]
    if not data.startswith(spec["magic"]):
        raise AppError(415, "CONTENT_MISMATCH", f"File content is not a valid {ext.upper()}")
    if spec["zip_marker"]:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                names = zf.namelist()
                if spec["zip_marker"] not in names:
                    raise AppError(415, "CONTENT_MISMATCH", f"File content is not a valid {ext.upper()}")
                if sum(i.file_size for i in zf.infolist()) > max_unzipped:
                    raise AppError(413, "ZIP_TOO_LARGE", "Decompressed size exceeds limit")
        except zipfile.BadZipFile as exc:
            raise AppError(415, "CONTENT_MISMATCH", f"File content is not a valid {ext.upper()}") from exc
    return ext
