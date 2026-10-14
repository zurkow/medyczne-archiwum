
# Medyczne Archiwum

Prywatne archiwum medyczne z kontami domowników i jawnymi uprawnieniami do
rekordów. Administrator może zarządzać zaproszeniami, ale nie otrzymuje dostępu
do prywatnych zasobów użytkowników. Aplikacja obsługuje zatwierdzone dokumenty,
ręczne pomiary oraz odpowiedzi asystenta oparte na dostępnych źródłach.

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

## Pomiary i asystent

Po zalogowaniu otwórz `/measurements/ui`, aby ręcznie dodać pomiar oraz
przeglądać historię. Wpis zawiera nazwę, wartość, jednostkę, datę i opcjonalną
notatkę. Filtr historii udostępnia zakresy 7, 30, 90 lub 365 dni. Wartości i
jednostki są przechowywane tak, jak zostały podane; aplikacja nie przypisuje
znaczenia medycznego ani nie konwertuje jednostek.

Wpisy pozostają prywatne, dopóki ich właściciel jawnie nie udzieli dostępu
konkretnemu kontu. Uprawnienie można nadać przez `POST
/measurements/{id}/grants` z `grantee_user_id`, a odwołać przez
`DELETE /measurements/{id}/grants/{grantee_user_id}`; żądania wymagają
zalogowanej sesji i tokenu CSRF. Dostęp grantee jest tylko do odczytu.
Administrator nie uzyskuje automatycznie dostępu do pomiarów, a odwołane
uprawnienie przestaje być uwzględniane przez asystenta.

Na stronie `/assistant/ui` można pytać o zatwierdzone dokumenty i pomiary
dostępne zalogowanemu użytkownikowi. Pomiary są pobierane osobnym zapytaniem
strukturalnym dla ostatnich 365 dni, wyłącznie w granicach aktualnych uprawnień;
ich wartości i notatki nie trafiają do indeksu wektorowego. Asystent może
przytoczyć zapisane nazwy, wartości, jednostki i daty, ale nie diagnozuje,
nie zaleca postępowania, nie przelicza jednostek i nie interpretuje trendów.
Notatki i tekst dokumentów są traktowane jako niezaufane dane, nie instrukcje.
Gdy nie ma pasującego, dostępnego źródła, asystent zwraca komunikat o braku
danych zamiast generować odpowiedź.

## Opcjonalne lokalne OCR i wyszukiwanie wektorowe

Doświadczenia z OCR i `sqlite-vec` wymagają 64-bitowego Pythona 3.12. Zainstaluj
opcjonalne zależności projektu:

```powershell
py -3.12 -m pip install -e ".[dev,local-ai]"
```

Grupa `local-ai` zawiera PyMuPDF (`pymupdf`) do obsługi PDF-ów. `sqlite-vec` do
wektorowego wyszukiwania w SQLite jest częścią podstawowej instalacji.
Weryfikuj wymagane filtrowanie partycji bezpośrednio w zapytaniu KNN poleceniem:

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

Indeksowanie wysyła tekst wyłącznie do lokalnego endpointu embeddingów LM Studio
(`MEDICAL_ARCHIVE_LM_STUDIO_BASE_URL`, domyślnie
`http://127.0.0.1:1234/v1`). Użyj modelu embeddingów zgodnego z wymiarem 768;
domyślny identyfikator `text-embedding-nomic-embed-text-v1.5` odpowiada modelowi
Nomic załadowanemu w LM Studio. Identyfikator chatu Bielik to
`bielik-1.5b-v3.0-instruct`; można go zmienić przez `MEDICAL_ARCHIVE_CHAT_MODEL`.
Obie nazwy można nadpisać zmiennymi `MEDICAL_ARCHIVE_EMBEDDING_MODEL` i
`MEDICAL_ARCHIVE_CHAT_MODEL`. Aplikacja odrzuca endpoint spoza loopback oraz nie
zapisuje treści dokumentu ani wektorów w logach.

Fragmentowanie nie pobiera ani nie wysyła tekstu do zewnętrznego tokenizera.
Ponieważ tokenizer modelu nie jest częścią lokalnych zależności, używa
deterministycznego lokalnego przybliżenia: ciągi Unicode złożone z liter/cyfr
dzieli na jednostki po maksymalnie cztery znaki, a każdą interpunkcję liczy jako
jedną jednostkę. To jawne przybliżenie, a nie deklaracja zgodności z tokenizerem
modelu. Fragmenty mają limit 500 takich jednostek i overlap 80; granice stron
pozostają zachowane.
