# Pomiary i pulpit pacjenta — plan implementacji

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dodać prywatne ręczne pomiary z tabelą i wykresem, pulpit pacjenta jako stronę startową oraz kontrolowane uwzględnianie pomiarów w odpowiedziach asystenta.

**Architecture:** Moduł pomiarów używa wspólnej polityki widoczności z planu 1 i udostępnia typowane, ograniczone czasowo rekordy do RAG z planu 2. Pulpit jest renderowany po stronie serwera; wykres to lokalny SVG, bez zewnętrznej biblioteki lub telemetrii.

**Tech Stack:** Fundament i moduły z planów 1–2, SQLAlchemy 2, Alembic, FastAPI, Jinja2, SVG, pytest.

---

## Mapa plików

- `src/medical_archive/measurements/{__init__,models,service,router}.py` — walidacja, zakres danych, CRUD i filtr dostępu.
- `src/medical_archive/templates/{dashboard,measurements}.html`, `src/medical_archive/static/app.css` — pulpit, formularze, tabela i SVG.
- `migrations/versions/0005_measurements.py` — schemat pomiarów.
- `src/medical_archive/rag/{retrieval,service}.py` — merge dozwolonych pomiarów i dokumentów, cytowanie przez typ źródła.
- `tests/measurements/{test_validation,test_access,test_trends}.py`, `tests/rag/test_measurement_sources.py`.

## Task 1: Model pomiarów z walidacją i autoryzacją

**Files:**
- Create: `src/medical_archive/measurements/__init__.py`, `models.py`, `service.py`, `router.py`
- Create: `migrations/versions/0005_measurements.py`
- Modify: `src/medical_archive/main.py`, `src/medical_archive/models/__init__.py`
- Create: `tests/measurements/test_validation.py`, `test_access.py`

- [ ] **Step 1: Dodaj failing testy zachowania i walidacji**

```python
# tests/measurements/test_validation.py
def test_create_measurement_normalizes_value_and_preserves_timestamp(owner_client):
    response = owner_client.post(
        "/measurements",
        json={
            "name": "Ciśnienie skurczowe",
            "value": "125.5",
            "unit": "mmHg",
            "measured_at": "2026-10-12T08:30:00+02:00",
            "note": "przed śniadaniem",
        },
    )
    assert response.status_code == 201
    assert response.json()["value"] == 125.5
    assert response.json()["unit"] == "mmHg"
    assert response.json()["measured_at"] == "2026-10-12T06:30:00Z"


def test_measurement_rejects_nan_and_blank_unit(owner_client):
    for value, unit in [("NaN", "mmHg"), ("1", " "), ("Infinity", "kg")]:
        response = owner_client.post(
            "/measurements",
            json={"name": "Pomiar", "value": value, "unit": unit, "measured_at": "2026-10-12T08:30:00Z"},
        )
        assert response.status_code == 422
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/measurements/test_validation.py -q`
Expected: FAIL — endpoint pomiaru nie istnieje.

- [ ] **Step 3: Zaimplementuj model i metody wyłącznie dla widocznych rekordów**

Model `Measurement(id, owner_user_id, name, value, unit, measured_at_utc, note, created_at)`. Wartość przyjmuj i waliduj jako `Decimal`, zapisuj w SQLite jako `Numeric(20, 8)` i odrzucaj więcej niż 12 cyfr przed separatorem lub 8 po nim; nigdy nie konwertuj przez `float` przed zapisem. Odrzucaj NaN, infinities, brak strefy czasowej, pustą nazwę/jednostkę i nadmiernie długie pola; czas normalizuj do UTC. Nie przypisuj jednostek ani znaczenia medycznego automatycznie.

Endpointy POST/GET/PUT/DELETE sprawdzają `visible_resource_ids` i `require_owner` z fundamentu; udostępniony wpis jest tylko do odczytu odbiorcy, edycję i usuwanie może wykonać właściciel. Lista wymaga ograniczenia czasu/paginacji i stabilnego sortowania (czas, ID). Błędy walidacji wskazują konkretne pole.

- [ ] **Step 4: Uruchom CRUD i autoryzację**

Run: `py -3.12 -m pytest tests/measurements/test_validation.py tests/measurements/test_access.py -q`
Expected: prawidłowy wpis zapisuje się w UTC; użytkownik obcy nie może czytać ani edytować; jawny odbiorca ma odczyt.

- [ ] **Step 5: Zapisz model i endpointy pomiarów**

Run: `git add src/medical_archive/measurements src/medical_archive/main.py src/medical_archive/models migrations/versions/0005_measurements.py tests/measurements/test_validation.py tests/measurements/test_access.py; git commit -m "feat: add private measurements"`
Expected: commit zawiera CRUD pomiarów, walidację wejścia i ACL.

## Task 2: Historia, SVG i pulpit wybrany przez użytkownika

**Files:**
- Modify: `src/medical_archive/measurements/service.py`, `router.py`
- Create: `src/medical_archive/measurements/trends.py`
- Create: `src/medical_archive/templates/measurements.html`, `dashboard.html`
- Modify: `src/medical_archive/web/router.py`, `src/medical_archive/templates/base.html`, `src/medical_archive/static/app.css`
- Create: `tests/measurements/test_trends.py`, `tests/test_dashboard.py`

- [ ] **Step 1: Dodaj failing testy sortowania i widoczności pulpitu**

```python
# tests/measurements/test_trends.py
def test_trend_contains_only_visible_points_in_chronological_order(trend_service, alice, measurements):
    result = trend_service.points(alice, name="Ciśnienie skurczowe", unit="mmHg", days=30)

    assert [point.measured_at for point in result] == sorted(
        point.measured_at for point in result
    )
    assert all(point.owner_id == alice.id or point.shared_with_id == alice.id for point in result)


# tests/test_dashboard.py
def test_dashboard_shows_recent_documents_and_measurements_only_for_current_user(alice_client):
    response = alice_client.get("/")
    assert response.status_code == 200
    assert "Ostatnie dokumenty" in response.text
    assert "Pomiary" in response.text
    assert "Zapytaj o swoje dokumenty" in response.text
    assert "private data belonging to bob" not in response.text
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/measurements/test_trends.py tests/test_dashboard.py -q`
Expected: FAIL — trend i docelowy pulpit nie zostały dodane.

- [ ] **Step 3: Dodaj limitowaną historię i semantyczny SVG**

`trends.py` zwraca punkty dla wybranej nazwy/jednostki z zakresu 7/30/90/365 dni; nie łączy jednostek ani różnych nazw, ogranicza wyniki i sortuje rosnąco po UTC. Widok listy oferuje ręczne dodanie, edycję/usunięcie własnych wpisów i filtr czasu. Wykres SVG generuje serwer na podstawie już autoryzowanych punktów, opisuje osie, jednostkę, etykiety i tabelaryczny fallback; nie używa zewnętrznego CDN.

Zastąp stronę startową pulpitem: dostępny profil (tylko własny/jawnie współdzielony), ostatnie dokumenty, skrót pomiarów i szybkie pole pytania. Pokaż błędy zapytań jawnie i nie umieszczaj danych osób niedostępnych w HTML. Nawigacja obsługuje ekran mobilny, klawiaturę i widoczny focus.

- [ ] **Step 4: Uruchom testy trendów i widoku**

Run: `py -3.12 -m pytest tests/measurements/test_trends.py tests/test_dashboard.py -q`
Expected: chronologia, filtry i uprawnienia PASS; HTML/SVG nie zawiera danych obcego użytkownika i jest użyteczny bez JavaScript.

- [ ] **Step 5: Zapisz pulpity, historię i wykres**

Run: `git add src/medical_archive/measurements src/medical_archive/templates src/medical_archive/web/router.py src/medical_archive/static/app.css tests/measurements/test_trends.py tests/test_dashboard.py; git commit -m "feat: add patient overview dashboard"`
Expected: commit zawiera historię, wykres SVG i wybrany przez użytkownika pulpit.

## Task 3: Pomiar jako bezpieczne źródło RAG

**Files:**
- Modify: `src/medical_archive/rag/retrieval.py`, `prompt.py`, `service.py`
- Create: `tests/rag/test_measurement_sources.py`
- Modify: `README.md`

- [ ] **Step 1: Dodaj failing test cytowania i separacji pomiarów**

```python
# tests/rag/test_measurement_sources.py
def test_answer_can_cite_visible_measurement_but_not_another_users_measurement(
    chat_service, alice, alice_measurement, bob_measurement
):
    result = chat_service.ask(alice, "Jakie ciśnienie zapisałem 12 października?")

    assert result.sources == [
        {
            "kind": "measurement",
            "measurement_id": alice_measurement.id,
            "measured_at": alice_measurement.measured_at.isoformat(),
        }
    ]
    assert bob_measurement.id not in str(result)
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/rag/test_measurement_sources.py -q`
Expected: FAIL — retriever zbiera tylko fragmenty dokumentów.

- [ ] **Step 3: Pobieraj pomiary przez strukturalne zapytanie z ACL**

Nie umieszczaj pomiarów w embeddingach/indeksie wektorowym: pobierz je osobnym SQL ograniczonym do widocznych user IDs/grantów i zakresu czasu. Przekaż ograniczone wpisy asystentowi razem ze źródłami dokumentów. Rozszerz typ cytowania o `measurement`, a link twórz przez aplikację do szczegółu wpisu; model może referować tylko ID z kontekstu. Przy braku wpisów wracaj do jawnego no-data z planu 2. Nie obliczaj diagnozy ani interpretacji klinicznej trendu; asystent podsumowuje wyłącznie wpisane wartości/daty.

- [ ] **Step 4: Uruchom testy łączenia retrieval**

Run: `py -3.12 -m pytest tests/rag tests/measurements -q`
Expected: źródła pomiarów cytowane; obce rekordy nie trafiają do promptu; istniejące cytowania dokumentów pozostają niezmienione.

- [ ] **Step 5: Uruchom pełny zestaw i aktualizuj instrukcje użytkownika**

README opisuje tworzenie wpisu, filtrowanie, udostępnianie, jednostki bez automatycznych konwersji i ograniczenie medyczne odpowiedzi.

Run: `py -3.12 -m pytest -q; py -3.12 -m ruff check .; py -3.12 -m ruff format --check .`
Expected: wszystkie testy PASS, Ruff bez błędów.

- [ ] **Step 6: Zapisz pomiary jako cytowane źródło asystenta**

Run: `git add src/medical_archive/rag README.md tests/rag/test_measurement_sources.py; git commit -m "feat: cite measurements in assistant answers"`
Expected: commit zawiera filtrowane źródła pomiarów i test cytowania.
