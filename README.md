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
