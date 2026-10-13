# Fundament i prywatne konta — plan implementacji

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dostarczyć uruchamialny fundament aplikacji, trwałą bazę SQLite i izolowane konta domowników z sesjami, zaproszeniami oraz jawnym udostępnianiem rekordów.

**Architecture:** FastAPI składa małe moduły domenowe. SQLAlchemy 2 i Alembic obsługują SQLite; każde zapytanie domenowe otrzymuje zalogowanego użytkownika i sprawdza własność lub jawne uprawnienie. Administrator obsługuje zaproszenia i konfigurację, ale nie uzyskuje dostępu do dokumentacji użytkowników.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, SQLite, Jinja2, argon2-cffi, pytest, HTTPX, Ruff.

---

## Mapa plików

- `pyproject.toml`, `.gitignore`, `README.md` — zależności, polecenia jakościowe i instrukcje deweloperskie.
- `src/medical_archive/config.py`, `db.py`, `main.py` — walidowana konfiguracja, fabryka sesji SQLite i składanie aplikacji.
- `src/medical_archive/models/{__init__,user,session,invitation,grant}.py` — tabele użytkownika, sesji, zaproszeń i jawnych udostępnień.
- `src/medical_archive/auth/{passwords,service,dependencies,router,bootstrap}.py` — logowanie, inicjalizacja administratora, zaproszenia, wylogowanie i zależność autoryzacji.
- `src/medical_archive/web/{router,templates}.py`, `templates/{base,login,invite}.html`, `static/app.css` — responsywny szkielet i formularze bez frameworka frontendowego.
- `migrations/env.py`, `migrations/versions/0001_identity.py` — schemat odtwarzalny z migracji.
- `tests/conftest.py`, `tests/auth/test_authentication.py`, `tests/auth/test_authorization.py` — testy integracyjne na tymczasowej bazie.

Fixture `migrated_engine` używa `tmp_path`, konfiguruje Alembic na prywatny plik testowy i wykonuje `upgrade head`; fixture klienta tworzy osobną bazę/migrację na test. Testy nie wskazują na domyślny katalog danych ani na bazę użytkownika.

## Task 1: Bootstrap aplikacji i testów

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`
- Create: `src/medical_archive/__init__.py`, `src/medical_archive/main.py`
- Create: `tests/conftest.py`, `tests/test_health.py`

- [ ] **Step 1: Dodaj failing test dla uruchomienia i health endpointu**

```python
# tests/test_health.py
from fastapi.testclient import TestClient

from medical_archive.main import create_app


def test_health_endpoint_returns_ready():
    response = TestClient(create_app(testing=True)).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
```

- [ ] **Step 2: Uruchom test i potwierdź oczekiwany brak modułu**

Run: `py -3.12 -m pytest tests/test_health.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'medical_archive'`.

- [ ] **Step 3: Dodaj minimalny pakiet i konfigurację narzędzi**

`pyproject.toml` musi definiować projekt `medyczne-archiwum`, `requires-python = ">=3.12,<3.13"`, zależności FastAPI, uvicorn, SQLAlchemy 2, Alembic, Jinja2, pydantic-settings, argon2-cffi oraz zestaw deweloperski pytest, httpx i ruff. Skonfiguruj editable install (`pip install -e ".[dev]"`), pytest z `pythonpath = ["src"]`, Ruff z linią 100 znaków.

```python
# src/medical_archive/main.py
from fastapi import FastAPI


def create_app(*, testing: bool = False) -> FastAPI:
    app = FastAPI(title="Medyczne Archiwum", docs_url=None, redoc_url=None)

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
```

`.gitignore` wyklucza `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.env`, katalog danych aplikacji, lokalną bazę, pliki użytkowników, certyfikaty i backupy. README ostrzega, by nie umieszczać prawdziwych danych medycznych w repozytorium.

- [ ] **Step 4: Zainstaluj pakiet i uruchom test**

Run: `py -3.12 -m pip install -e ".[dev]"; py -3.12 -m pytest tests/test_health.py -q`
Expected: PASS, `1 passed`.

- [ ] **Step 5: Sprawdź formatowanie i typowe błędy**

Run: `py -3.12 -m ruff check .; py -3.12 -m ruff format --check .`
Expected: oba polecenia kończą się kodem `0`.

- [ ] **Step 6: Zapisz działający szkielet**

Run: `git add pyproject.toml .gitignore README.md src/medical_archive/__init__.py src/medical_archive/main.py tests/conftest.py tests/test_health.py; git commit -m "build: bootstrap medical archive app"`
Expected: commit zawiera tylko szkielet aplikacji i konfigurację narzędzi.

## Task 2: Konfiguracja, SQLite i pierwsza migracja

**Files:**
- Create: `src/medical_archive/config.py`, `src/medical_archive/db.py`
- Create: `src/medical_archive/models/__init__.py`, `src/medical_archive/models/user.py`, `session.py`, `invitation.py`, `grant.py`
- Create: `alembic.ini`, `migrations/env.py`, `migrations/versions/0001_identity.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Dodaj failing testy konfiguracji i migracji**

```python
# tests/test_database.py
from sqlalchemy import inspect

from medical_archive.config import Settings
def test_migration_creates_identity_schema_in_sqlite(migrated_engine):
    names = set(inspect(migrated_engine).get_table_names())

    assert {"users", "sessions", "invitations", "access_grants"} <= names


def test_settings_rejects_empty_session_secret():
    try:
        Settings(session_secret="")
    except ValueError as error:
        assert "session_secret" in str(error)
    else:
        raise AssertionError("Empty session secret must be rejected")
```

- [ ] **Step 2: Uruchom test i potwierdź brak konfiguracji**

Run: `py -3.12 -m pytest tests/test_database.py -q`
Expected: FAIL — brak `medical_archive.config`.

- [ ] **Step 3: Zaimplementuj konfigurację i minimalny schemat domenowy**

W `config.py` użyj `BaseSettings`, wymagaj jawnej wartości `session_secret` (minimum 32 losowe bajty zakodowane jako tekst) poza trybem testowym, waliduj `data_dir` i domyślny SQLite w tym katalogu. Nie umieszczaj prawdziwej wartości sekretu w kodzie. W `db.py` skonfiguruj SQLite z `check_same_thread=False`, włącz `PRAGMA foreign_keys=ON` na każdym połączeniu i udostępnij `create_database(url)` oraz zależność `get_session`.

Modele: `User(id, email, password_hash, is_admin, created_at)`; `LoginSession(token_hash, user_id, expires_at)`; `Invitation(token_hash, expires_at, used_at, created_by_user_id)`; `AccessGrant(id, owner_user_id, grantee_user_id, resource_type, resource_id, created_at)`. Unikalny email (case-insensitive), unikalny skrót sesji i zaproszenia, FK z `ON DELETE CASCADE`, kontrola, że właściciel i odbiorca nie są tym samym użytkownikiem. Admin nie ma relacji dostępu do dokumentów.

Dodaj Alembic z `target_metadata` z modeli. Pierwsza migracja jawnie tworzy tabele, indeksy i ograniczenia; migracja wstecz usuwa je w odwrotnej kolejności.

- [ ] **Step 4: Uruchom migrację i testy bazy**

Run: `$env:MEDICAL_ARCHIVE_DATABASE_URL='sqlite:///./plan-test.db'; py -3.12 -m alembic upgrade head; py -3.12 -m pytest tests/test_database.py -q`
Expected: migracja `0001_identity` zastosowana, `2 passed`.

- [ ] **Step 5: Zweryfikuj odwracalność migracji i format**

Run: `py -3.12 -m alembic downgrade base; py -3.12 -m alembic upgrade head; py -3.12 -m ruff check .`
Expected: downgrade i ponowne upgrade bez błędów; Ruff kończy się kodem `0`.

- [ ] **Step 6: Zapisz schemat i migrację**

Run: `git add src/medical_archive/config.py src/medical_archive/db.py src/medical_archive/models alembic.ini migrations tests/test_database.py; git commit -m "feat: add sqlite identity schema"`
Expected: commit zawiera modele, konfigurację bazy i migrację tożsamości.

## Task 3: Hasła, sesje, zaproszenia i bootstrap administratora

**Files:**
- Create: `src/medical_archive/auth/__init__.py`, `passwords.py`, `service.py`, `dependencies.py`, `router.py`
- Modify: `src/medical_archive/main.py`
- Create: `tests/auth/test_authentication.py`

- [ ] **Step 1: Dodaj failing testy zachowań logowania**

```python
# tests/auth/test_authentication.py
def test_login_sets_opaque_secure_cookie_and_logout_revokes_it(client, admin_user):
    response = client.post(
        "/auth/login",
        data={"email": admin_user.email, "password": "correct horse battery"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.cookies["session"].value
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()

    client.cookies.update(response.cookies)
    assert client.get("/").status_code == 200
    assert client.post("/auth/logout").status_code == 303
    assert client.get("/").status_code == 303


def test_invalid_password_does_not_create_session(client, admin_user):
    response = client.post(
        "/auth/login",
        data={"email": admin_user.email, "password": "incorrect"},
    )
    assert response.status_code == 401
    assert "session" not in response.cookies
```

- [ ] **Step 2: Uruchom test i potwierdź brak routera**

Run: `py -3.12 -m pytest tests/auth/test_authentication.py -q`
Expected: FAIL — fixture/router autoryzacji jeszcze nie istnieje.

- [ ] **Step 3: Zaimplementuj bezpieczne haszowanie i nieprzewidywalne tokeny**

`passwords.py` używa Argon2id z parametrami biblioteki i udostępnia wyłącznie `hash_password` oraz `verify_password`. `service.py` zapewnia atomowe zużycie zaproszenia oraz operacje tworzenia konta, logowania, walidacji i unieważniania sesji. Token sesji i zaproszenia generuj `secrets.token_urlsafe(32)`; zapisuj `SHA256(token)` zamiast tokenu jawnego. Ciasteczko sesji ustawiaj jako `HttpOnly`, `Secure`, `SameSite=Strict`, ogranicz do ścieżki `/` i maksymalnie 12 godzin. Błędne logowanie zwraca ten sam komunikat dla nieznanego konta i złego hasła. Formularze POST sprawdzają token CSRF przechowywany w sesji.

Dodaj jawne polecenie `python -m medical_archive.auth.bootstrap --email ...`; wczytuje hasło interaktywnie bez echa, tworzy tylko pierwszego administratora i odmawia ponownego bootstrapu. Administrator tworzy zaproszenie o ograniczonym czasie życia; surowy token wyświetla się raz. Rejestracja zużywa zaproszenie w transakcji i nie daje roli administratora.

Podłącz widoki `/auth/login`, `/auth/logout`, `/auth/invite`, `/auth/register` i `/`. Brak sesji przekierowuje do logowania; endpointy admina wymagają roli. Dokumentacja API OpenAPI pozostaje wyłączona.

- [ ] **Step 4: Uruchom testy autoryzacji**

Run: `py -3.12 -m pytest tests/auth/test_authentication.py -q`
Expected: wszystkie testy logowania, wylogowania, expiracji i jednorazowego zaproszenia PASS.

- [ ] **Step 5: Uruchom Ruff i cały dotychczasowy zestaw**

Run: `py -3.12 -m pytest -q; py -3.12 -m ruff check .`
Expected: wszystkie testy PASS, Ruff kod `0`.

- [ ] **Step 6: Zapisz działające logowanie i zaproszenia**

Run: `git add src/medical_archive/auth src/medical_archive/main.py src/medical_archive/web src/medical_archive/templates tests/auth/test_authentication.py; git commit -m "feat: add secure account sessions"`
Expected: commit zawiera hasła, sesje, zaproszenia i widoki logowania.

## Task 4: Jawne udostępnienia i negatywne testy autoryzacji

**Files:**
- Create: `src/medical_archive/auth/grants.py`, `src/medical_archive/auth/grant_router.py`
- Modify: `src/medical_archive/main.py`, `src/medical_archive/auth/dependencies.py`
- Create: `tests/auth/test_authorization.py`

- [ ] **Step 1: Dodaj failing test, że admin nie odczytuje cudzego zasobu**

```python
# tests/auth/test_authorization.py
def test_admin_has_no_resource_access_and_grant_is_revoked_immediately(
    grant_service, session, owner_user, admin_user, recipient_user, private_resource
):
    assert not grant_service.can_access(
        session, user_id=admin_user.id, resource_type="test", resource_id=private_resource.id
    )
    assert not grant_service.can_access(
        session, user_id=recipient_user.id, resource_type="test", resource_id=private_resource.id
    )

    grant = grant_service.create(
        session,
        owner_user_id=owner_user.id,
        grantee_user_id=recipient_user.id,
        resource_type="test",
        resource_id=private_resource.id,
        verify_owner=lambda resource_id, user_id: (
            resource_id == private_resource.id and user_id == owner_user.id
        ),
    )
    assert grant_service.can_access(
        session, user_id=recipient_user.id, resource_type="test", resource_id=private_resource.id
    )
    assert not grant_service.can_access(
        session, user_id=admin_user.id, resource_type="test", resource_id=private_resource.id
    )

    grant_service.revoke(session, grant_id=grant.id, owner_user_id=owner_user.id)
    assert not grant_service.can_access(
        session, user_id=recipient_user.id, resource_type="test", resource_id=private_resource.id
    )
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/auth/test_authorization.py -q`
Expected: FAIL — serwis `grant_service` i funkcje polityki dostępu nie istnieją.

- [ ] **Step 3: Zaimplementuj jedną, współdzieloną politykę widoczności**

`grants.py` udostępnia `visible_resource_ids(session, *, user_id, resource_type)`, `can_access(session, *, user_id, resource_type, resource_id)`, `require_owner(session, *, user_id, resource_type, resource_id)` oraz `create(..., verify_owner=...)`. Odczyt jest dozwolony właścicielowi albo odbiorcy aktywnego grantu; rola administratora nie omija sprawdzania. Operacja utworzenia grantu wymaga callbacku domenowego potwierdzającego właściciela rekordu; domeny dokumentów/pomiarów podłączą własny check przy rejestracji endpointu. Cofnąć grant może tylko właściciel. Nie ujawniaj istnienia nieudostępnionych zasobów; odpowiedź to 404. Odbiorca nie może dalej udostępniać otrzymanego rekordu. Test w tym planie sprawdza service-level policy na tymczasowym typie zasobu; endpointy HTTP i sprawdzenie realnego właściciela pokrywają plany 2 i 3.

Dodaj ogólną stronę główną Jinja2 z nawigacją dla dostępnych modułów, polskimi etykietami, etykietami formularzy i czytelnymi błędami. `static/app.css` stosuje układ mobile-first, widoczny focus klawiatury i kontrast tekstu co najmniej 4.5:1.

- [ ] **Step 4: Uruchom pozytywne i negatywne testy uprawnień**

Run: `py -3.12 -m pytest tests/auth/test_authorization.py tests/auth/test_authentication.py -q`
Expected: właściciel i jawny odbiorca widzą dozwolone zasoby; admin i nieuprawniony użytkownik dostają 404; cofnięcie grantu działa natychmiast.

- [ ] **Step 5: Zapisz aktualne uruchomienie i ograniczenia w README**

Dokumentuj inicjalizację środowiska, ustawienie `MEDICAL_ARCHIVE_SESSION_SECRET`, `alembic upgrade head`, polecenie bootstrap administratora, `uvicorn medical_archive.main:app` i testy. Wyraźnie zaznacz: aplikacja nie jest jeszcze gotowa na prawdziwe dane, dopóki nie ma HTTPS i procesu backupu z planu 4.

Run: `py -3.12 -m pytest -q; py -3.12 -m ruff check .; py -3.12 -m ruff format --check .`
Expected: wszystkie testy PASS, oba sprawdzenia Ruff kod `0`.

- [ ] **Step 6: Zapisz reguły dostępu i szkielet UI**

Run: `git add src/medical_archive/auth src/medical_archive/main.py src/medical_archive/web src/medical_archive/templates src/medical_archive/static tests/auth/test_authorization.py README.md; git commit -m "feat: enforce explicit resource access"`
Expected: commit zawiera współdzielone reguły ACL, testy odmowy dostępu i UI szkieletu.
