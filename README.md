# Medyczne Archiwum

Prywatne archiwum medyczne z kontami domowników i jawnymi uprawnieniami do
rekordów. Administrator może zarządzać zaproszeniami, ale nie otrzymuje dostępu
do prywatnych zasobów użytkowników. Moduły dokumentów i pomiarów nie są jeszcze
dostępne.

## Wymagania

- Python 3.12

## Konfiguracja i uruchomienie

W katalogu repozytorium utwórz i aktywuj wirtualne środowisko, a następnie
zainstaluj pakiet wraz z narzędziami deweloperskimi:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Ustaw unikatowy sekret sesji (co najmniej 32 losowe bajty zakodowane jako
tekst). Nie zapisuj go w repozytorium:

```powershell
$env:MEDICAL_ARCHIVE_SESSION_SECRET = (python -c "import secrets; print(secrets.token_urlsafe(32))")
```

Aplikacja domyślnie przechowuje bazę SQLite w katalogu danych użytkownika.
Utwórz schemat i skonfiguruj pierwsze konto administratora:

```powershell
python -m alembic upgrade head
python -m medical_archive.auth.bootstrap --email admin@example.test
```

Polecenie bootstrap pobiera hasło interaktywnie i może utworzyć wyłącznie
pierwszego administratora. Uruchom serwer:

```powershell
python -m uvicorn medical_archive.main:app
```

Strona logowania jest dostępna pod adresem `http://127.0.0.1:8000/auth/login`;
endpoint `GET /health` zwraca `{"status": "ok"}`. Przy pierwszym logowaniu
administrator może utworzyć jednorazowy link zaproszenia dla kolejnego konta.

## Testy i kontrole jakości

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

**Nie umieszczaj w repozytorium prawdziwych danych medycznych ani danych
użytkowników.** Aplikacja nie jest gotowa do przechowywania prawdziwych danych
medycznych, dopóki nie zostanie skonfigurowane HTTPS/TLS i sprawdzony proces
tworzenia oraz odtwarzania kopii zapasowych. Lokalny serwer deweloperski nie
zapewnia tych zabezpieczeń.

## Opcjonalne lokalne OCR i wyszukiwanie wektorowe

Doświadczenia z OCR i `sqlite-vec` wymagają 64-bitowego Pythona 3.12. Zainstaluj
opcjonalne zależności projektu:

```powershell
py -3.12 -m pip install -e ".[dev,local-ai]"
```

Grupa `local-ai` zawiera PyMuPDF (`pymupdf`) do obsługi PDF-ów oraz
`sqlite-vec` do wektorowego wyszukiwania w SQLite. Weryfikuj wymagane filtrowanie
partycji bezpośrednio w zapytaniu KNN poleceniem:

```powershell
py -3.12 -m pytest tests/rag/test_sqlite_vec_compatibility.py -q
```

OCR wymaga osobnej instalacji Tesseract OCR dla Windows i pakietu językowego
`pol.traineddata`. Dodaj katalog binarny Tesseract do `PATH`; jeśli nie jest to
możliwe, ustaw `TESSERACT_CMD` na ścieżkę do `tesseract.exe`. Gdy pliki danych
językowych nie są wykrywane automatycznie, ustaw `TESSDATA_PREFIX` na katalog
zawierający te pliki.

Do diagnostyki LM Studio uruchom lokalny serwer API na loopback, domyślnie na
porcie `1234`, i załaduj co najmniej jeden model. Inny port można ustawić przed
uruchomieniem narzędzia:

```powershell
$env:LM_STUDIO_PORT = "1234"
py -3.12 tools/check_local_ai.py
```

`check_local_ai.py` sprawdza wersję Pythona i SQLite, załadowanie `sqlite-vec`,
PyMuPDF, Tesseract wraz z `pol.traineddata` oraz nazwy modeli z
`http://127.0.0.1:<port>/v1/models`. Wysyła wyłącznie lokalne żądanie HTTP GET
bez treści dokumentu. Brak Tesseracta, danych polskiego OCR, LM Studio lub
załadowanego modelu jest jawnie oznaczany jako brak wymagania i kończy narzędzie
kodem 1; nie jest przedstawiany jako udana konfiguracja.
