# Medyczne Archiwum

Minimalny fundament prywatnego archiwum medycznego. Aplikacja udostępnia na tym
etapie jedynie endpoint zdrowia; konta i pozostałe funkcje są planowane.

## Wymagania

- Python 3.12

## Środowisko deweloperskie

W katalogu repozytorium utwórz i aktywuj wirtualne środowisko, a następnie
zainstaluj pakiet wraz z narzędziami deweloperskimi:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Uruchom testy i sprawdzenia jakości:

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Serwer deweloperski można uruchomić poleceniem:

```powershell
python -m uvicorn medical_archive.main:app
```

Endpoint `GET /health` zwraca `{"status": "ok"}`.

**Nie umieszczaj w repozytorium prawdziwych danych medycznych ani danych
użytkowników.** Ten wczesny szkielet nie jest przygotowany do przechowywania
prawdziwych danych medycznych.

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
