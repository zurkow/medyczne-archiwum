"""Check local OCR and LM Studio prerequisites without sending document content."""

from medical_archive import preflight

if __name__ == "__main__":
    raise SystemExit(preflight.main(["--strict"]))
