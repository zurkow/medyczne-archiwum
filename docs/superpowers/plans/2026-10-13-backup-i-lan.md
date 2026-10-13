# Backup, bezpieczny LAN i gotowość do użycia — plan implementacji

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Umożliwić bezpieczny, ręczny backup/restore lokalnej bazy i oryginałów, dostęp HTTPS wyłącznie z LAN oraz weryfikację kryteriów wydania bez wysyłania danych na zewnątrz.

**Architecture:** Administrator może tworzyć/odtwarzać pełny backup całego magazynu; użytkownik zaakceptował, że administrator znający hasło odszyfrowujące może w ten sposób odczytać dane wszystkich profili. Zwykłe UI, API i RAG nadal nie dają administratorowi dostępu do cudzych wpisów. Backup to spójny, wersjonowany kontener AES-256-GCM z kluczem pochodzącym przez scrypt; restore weryfikuje uwierzytelnienie przed zmianą bieżących danych i odtwarza przez atomowe przełączenie. Aplikacja nasłuchuje wyłącznie na skonfigurowanym interfejsie LAN, a LM Studio nadal tylko na loopback.

**Tech Stack:** Plany 1–3, Python `cryptography`, SQLite, FastAPI/Uvicorn, certyfikat lokalnego CA (mkcert), pytest.

---

## Mapa plików

- `src/medical_archive/backup/{__init__,format,crypto,service,router}.py` — archiwizacja, AEAD, weryfikacja i UI.
- `src/medical_archive/config.py`, `src/medical_archive/main.py` — ścieżki runtime, bind address, HTTPS i fail-closed konfiguracja.
- `src/medical_archive/templates/backup.html`, `src/medical_archive/static/app.css` — formularze i komunikaty.
- `tests/backup/{test_crypto,test_roundtrip,test_restore_safety}.py`, `tests/test_network_config.py` — testy błędnego hasła, uszkodzeń, spójności i bindu.
- `scripts/{run-lan.ps1,restore-backup.ps1,check-release.ps1}` — polecenia lokalnego uruchomienia, odzyskiwania i weryfikacji.
- `README.md` — TLS LAN, firewall, odzyskiwanie i ostrzeżenia.

## Task 1: Format szyfrowanego backupu i roundtrip

**Files:**
- Modify: `pyproject.toml`
- Create: `src/medical_archive/backup/__init__.py`, `format.py`, `crypto.py`, `service.py`
- Create: `tests/backup/test_crypto.py`, `test_roundtrip.py`

- [ ] **Step 1: Dodaj failing testy roundtrip i integralności**

```python
# tests/backup/test_crypto.py
import pytest

from medical_archive.backup.crypto import InvalidBackup, InvalidBackupPassword


def test_backup_decrypts_with_correct_password_and_rejects_wrong_password(backup_crypto, payload):
    encrypted = backup_crypto.encrypt(payload, password="long correct horse battery staple")

    assert backup_crypto.decrypt(encrypted, password="long correct horse battery staple") == payload
    with pytest.raises(InvalidBackupPassword):
        backup_crypto.decrypt(encrypted, password="wrong password")


def test_backup_rejects_modified_ciphertext_without_returning_partial_plaintext(backup_crypto, payload):
    encrypted = bytearray(backup_crypto.encrypt(payload, password="long correct horse battery staple"))
    encrypted[-1] ^= 1

    with pytest.raises(InvalidBackup):
        backup_crypto.decrypt(bytes(encrypted), password="long correct horse battery staple")
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/backup/test_crypto.py -q`
Expected: FAIL — format backup nie jest jeszcze zaimplementowany.

- [ ] **Step 3: Zaimplementuj wersjonowany envelope i uwierzytelnione szyfrowanie**

Dodaj `cryptography`. Format: magic bytes `MARCHIVE`, version `1`, długość soli, sól 16 bajtów, nonce 12 bajtów, następnie ciphertext/tag AES-GCM. Klucz wyprowadzaj z UTF-8 hasła przez scrypt ze stałymi, udokumentowanymi parametrami `N=2**15`, `r=8`, `p=1`, długością 32 bajtów oraz limitem pamięci 64 MiB. Envelope ma ścisły limit rozmiaru, obsługiwaną wersję i walidację długości oraz obciętego wejścia przed odszyfrowaniem. Payload to strumieniowany ZIP z `manifest.json`, spójnym snapshotem całej bazy i oryginalnymi plikami wszystkich kont; manifest zawiera UUID, wersje schematu i względne wewnętrzne storage keys, nigdy absolutne ścieżki. Użyj SQLite online backup API zamiast kopiowania aktywnego pliku `.db`, aby uwzględnić WAL. Tymczasowy plaintext trzymaj wyłącznie w prywatnym katalogu tymczasowym i usuwaj w `finally`; logi nie zawierają hasła ani treści. Pełny eksport jest operacją administratora i przed wykonaniem wymaga jawnego potwierdzenia, że osoba znająca hasło backupu może odszyfrować dane wszystkich kont.

- [ ] **Step 4: Uruchom testy kryptografii i roundtrip**

Run: `py -3.12 -m pytest tests/backup/test_crypto.py tests/backup/test_roundtrip.py -q`
Expected: poprawny roundtrip PASS; błędne hasło i zmieniony tag zwracają typowany błąd bez ujawnienia plaintext.

- [ ] **Step 5: Zapisz działający format backupu**

Run: `git add pyproject.toml src/medical_archive/backup tests/backup/test_crypto.py tests/backup/test_roundtrip.py; git commit -m "feat: add encrypted backup format"`
Expected: commit zawiera wyłącznie zaimplementowany format i jego testy.

## Task 2: Bezpieczny restore i UI backup

**Files:**
- Modify: `src/medical_archive/backup/service.py`
- Create: `src/medical_archive/backup/router.py`, `src/medical_archive/templates/backup.html`
- Create: `tests/backup/test_restore_safety.py`
- Modify: `src/medical_archive/main.py`

- [ ] **Step 1: Dodaj failing testy na błędny backup i bezpieczne odtworzenie**

```python
# tests/backup/test_restore_safety.py
import pytest

from medical_archive.backup.crypto import InvalidBackup
def test_bad_backup_never_replaces_existing_database(backup_client, live_database, corrupt_backup):
    original = live_database.read_bytes()
    response = backup_client.post(
        "/settings/backup/restore",
        data={"password": "long correct horse battery staple"},
        files={"file": ("backup.marchive", corrupt_backup, "application/octet-stream")},
    )

    assert response.status_code == 422
    assert live_database.read_bytes() == original


def test_restore_rejects_path_traversal_entry_before_database_swap(restore_service, traversal_archive):
    with pytest.raises(InvalidBackup):
        restore_service.validate_payload(traversal_archive)
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/backup/test_restore_safety.py -q`
Expected: FAIL — endpoint i bezpieczna walidacja archiwum jeszcze nie istnieją.

- [ ] **Step 3: Wdróż dwuetapowe odtwarzanie**

Restore najpierw w całości odszyfrowuje archiwum do prywatnego katalogu tymczasowego, sprawdza AEAD, manifest, wersje, integralność SQLite, wymagane/nieoczekiwane ścieżki oraz brak ścieżek absolutnych i wpisów `..`. Dopóki walidacja nie zakończy się pomyślnie, aktywne pliki pozostają nietknięte. Następnie przełącz aplikację w maintenance mode: odrzuć nowe żądania zmieniające dane, opróżnij pule połączeń SQLAlchemy, zamknij połączenia SQLite i checkpointuj WAL. Utwórz rollback snapshot na tym samym woluminie, atomowo przełącz zweryfikowaną bazę i pliki, otwórz nowe połączenia i sprawdź stan; rollback usuń dopiero po sukcesie. Jeśli przełączenie lub sprawdzenie zawiedzie, przywróć rollback przed przywróceniem ruchu. Błędne archiwum nie może nadpisać aktywnych danych. Endpointy pełnego eksportu i restore są dostępne tylko administratorowi; nie dają mu widoku rekordów w zwykłym UI. Wyświetl administratorowi ostrzeżenie, że pełny backup wraz ze znanym hasłem pozwala odczytać dane wszystkich kont.

UI pozwala administratorowi utworzyć zaszyfrowany backup całego lokalnego magazynu i odtworzyć go po podaniu hasła. Eksport/restore wymagają ochrony CSRF, ponownego wpisania hasła konta i wyraźnego potwierdzenia zastąpienia bieżących danych. Ograniczaj upload, nigdy nie loguj hasła i nie dołączaj backupu automatycznie do odpowiedzi HTTP bez jawnej akcji administratora.

- [ ] **Step 4: Uruchom testy błędów, roundtrip i awaryjnego restore**

Run: `py -3.12 -m pytest tests/backup -q`
Expected: poprawny backup odtwarza konta, metadane, indeks wektorowy i oryginały; błędne hasło, uszkodzony tag, niebezpieczna ścieżka i niezgodna wersja nie zmieniają bieżącej bazy.

- [ ] **Step 5: Zapisz bezpieczne odtwarzanie i ekran backupu**

Run: `git add src/medical_archive/backup src/medical_archive/main.py src/medical_archive/templates/backup.html tests/backup; git commit -m "feat: add verified backup restore"`
Expected: commit zawiera walidację archiwum, rollback restore i ostrzeżenie dla administratora.

## Task 3: TLS i bind wyłącznie w zaufanej sieci LAN

**Files:**
- Modify: `src/medical_archive/config.py`, `src/medical_archive/main.py`
- Create: `scripts/run-lan.ps1`, `tests/test_network_config.py`
- Modify: `.gitignore`, `README.md`

- [ ] **Step 1: Dodaj failing testy dla bezpiecznych adresów**

```python
# tests/test_network_config.py
import pytest

from pydantic import ValidationError

from medical_archive.config import Settings


def test_production_config_refuses_public_bind_and_missing_tls():
    with pytest.raises(ValidationError):
        Settings(environment="production", host="0.0.0.0", tls_cert=None, tls_key=None)


def test_lm_studio_url_must_remain_loopback():
    with pytest.raises(ValidationError):
        Settings(lm_studio_base_url="http://192.168.1.10:1234/v1")
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/test_network_config.py -q`
Expected: FAIL — środowisko produkcyjne i URL modeli nie mają gate checks.

- [ ] **Step 3: Wymuś fail-closed TLS oraz loopback modelu**

Walidator produkcyjny wymaga certyfikatu/klucza TLS, silnego sekretu sesji i jawnego adresu bind z prywatnego zakresu LAN (nie `0.0.0.0`, loopback, multicast ani public IP). Adres LM Studio akceptuje wyłącznie `localhost`, `127.0.0.1` lub `::1`; HTTPS na localhost jest dozwolone. Nie ustawiaj wildcard CORS; wyłącz docs, ogranicz Host do lokalnego hostname/IP, sprawdzaj CSRF dla wszystkich formularzy zmieniających dane. Uvicorn uruchamiaj z certyfikatem/kluczem TLS; klucze i certyfikaty wyklucz z git. `run-lan.ps1` weryfikuje wymagane ustawienia i uruchamia aplikację na wybranym prywatnym IP i porcie.

README opisuje utworzenie certyfikatu przez zaufany lokalny CA (np. mkcert), instalację certyfikatu CA na każdym urządzeniu klienckim, ochronę klucza TLS, regułę zapory Windows tylko dla prywatnej podsieci oraz weryfikację, że port aplikacji jest zamknięty dla profilu Public, a LM Studio nasłuchuje wyłącznie na localhost. Nie generuj po cichu certyfikatu self-signed, któremu przeglądarka nie ufa.

- [ ] **Step 4: Uruchom negatywne testy konfiguracji i sprawdź granice LAN**

Run: `py -3.12 -m pytest tests/test_network_config.py -q; py -3.12 -m ruff check .`
Expected: public bind, brak TLS i zdalny endpoint modelu są odrzucane; lokalna konfiguracja deweloperska nadal działa przez TestClient.

- [ ] **Step 5: Zapisz konfigurację TLS dla LAN**

Run: `git add src/medical_archive/config.py src/medical_archive/main.py scripts/run-lan.ps1 tests/test_network_config.py .gitignore README.md; git commit -m "feat: serve app over trusted lan tls"`
Expected: commit zawiera fail-closed TLS, kontrolę bindu LAN i instrukcje klienta.

## Task 4: Końcowe kontrole wydania i instrukcje odtwarzania

**Files:**
- Create: `scripts/check-release.ps1`
- Modify: `README.md`, `src/medical_archive/templates/backup.html`
- Create: `tests/test_release_guards.py`

- [ ] **Step 1: Dodaj failing release guard**

```python
# tests/test_release_guards.py
import pytest

from pydantic import ValidationError

def test_production_start_refuses_missing_tls_configuration(settings_factory):
    with pytest.raises(ValidationError, match="tls_cert"):
        settings_factory(
            environment="production",
            host="192.168.1.10",
            session_secret="s" * 64,
            tls_cert=None,
            tls_key=None,
        )


def test_preflight_reports_missing_local_ai_without_hiding_the_degraded_state(ai_preflight):
    result = ai_preflight.check()
    assert result.status == "degraded"
    assert "embedding" in result.missing_components or "generation" in result.missing_components
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/test_release_guards.py -q`
Expected: FAIL — kontrola preflight nie istnieje.

- [ ] **Step 3: Zaimplementuj jawną kontrolę preflight i instrukcje awaryjne**

Preflight podaje dokładnie brakujące elementy: rozszerzenie SQLite, binarkę OCR i polski pakiet językowy, model embeddingowy, model generujący, certyfikat TLS albo uprawnienia katalogu danych. Brak bazy, `sqlite-vec`, prawidłowego sekretu lub TLS w profilu produkcyjnym zatrzymuje start; brak OCR/modelu utrzymuje aplikację w jawnym stanie ograniczonym, a odpowiedni import/asystent pokazuje błąd zamiast udawać sukces. Preflight nie wysyła dokumentów ani pomiarów do testowego promptu AI. `check-release.ps1` uruchamia Ruff, sprawdzenie formatowania, pytest, upgrade/downgrade/upgrade Alembic na tymczasowej bazie, negatywne testy uprawnień i preflight. README zawiera procedurę aktualizacji, wykonania backupu przed migracją, weryfikacji backupu i odtwarzania krok po kroku, utraty hasła oraz kontroli izolacji sieciowej.

- [ ] **Step 4: Uruchom bramkę wydania i ręczną kontrolę LAN**

Run: `.\scripts\check-release.ps1`
Expected: wszystkie testy automatyczne PASS. Ręczna kontrola: z telefonu w zaufanej sieci LAN otwórz HTTPS, zaloguj się niezależnym kontem, potwierdź brak dostępu do innego profilu, otwórz oryginalny PDF przez endpoint z autoryzacją i zadaj pytanie z cytowaniem. Z zewnętrznej sieci port aplikacji pozostaje zamknięty; port LM Studio jest zamknięty na wszystkich interfejsach poza loopback.

- [ ] **Step 5: Sprawdź backup/restore na fikcyjnych danych**

Utwórz dwa testowe konta, fikcyjny PDF bez danych osobowych i jeden wpis pomiaru; stwórz archiwum, usuń wyłącznie sprawdzoną bazę testową, odtwórz backup i ponownie uruchom testy dostępu/źródeł. Nie używaj prawdziwych danych medycznych do testów odbiorczych.

Run: `py -3.12 -m pytest -q; py -3.12 -m ruff check .; py -3.12 -m ruff format --check .`
Expected: pełny zestaw testów PASS; `git status --short` nie pokazuje baz, plików użytkowników, certyfikatów, haseł ani backupów.

- [ ] **Step 6: Zapisz bramkę wydania i runbook**

Run: `git add scripts/check-release.ps1 README.md tests/test_release_guards.py src/medical_archive; git commit -m "docs: add local deployment release checks"`
Expected: commit zawiera kontrole preflight, release gate i instrukcje bezpiecznego odtworzenia.
