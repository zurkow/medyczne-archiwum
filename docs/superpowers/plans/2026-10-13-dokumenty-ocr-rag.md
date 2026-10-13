# Dokumenty, OCR i lokalny RAG — plan implementacji

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Dodać prywatne archiwum PDF-ów i obrazów, potwierdzany import z lokalnym OCR oraz odpowiedzi Bielika oparte wyłącznie na dostępnych źródłach i cytowane dokumentami.

**Architecture:** Moduł dokumentów przechowuje oryginały poza webrootem, ekstrahuje tekst lokalnie i wymaga akceptacji metadanych przed indeksem. RAG najpierw ogranicza dane do właściciela i jawnie udostępnionych rekordów, a następnie wyszukuje je w segmencie użytkownika `sqlite-vec`; model jest osiągany wyłącznie przez lokalne API LM Studio.

**Tech Stack:** Fundament z planu 1, PyMuPDF do tekstu i renderowania PDF, Tesseract OCR z polskim `pol.traineddata`, `sqlite-vec`, LM Studio OpenAI-compatible API, pytest.

---

## Mapa plików

- `src/medical_archive/documents/{models,storage,extraction,service,router}.py` — rekordy, bezpieczne pliki, ekstrakcja, workflow importu i endpointy.
- `src/medical_archive/rag/{models,index,embeddings,retrieval,prompt,service,router}.py` — chunkowanie, prywatny indeks wektorowy, retriever i kontrakt odpowiedzi.
- `src/medical_archive/templates/{dashboard,documents,document_review,chat}.html`, `static/app.css` — pulpity, lista/detale i rozmowa ze źródłami.
- `migrations/versions/0002_documents.py`, `0003_document_text.py`, `0004_vector_index.py` — tabele dokumentów, tekst ekstrakcji i indeks wektorowy.
- `tests/documents/{test_upload,test_extraction,test_approval,test_access}.py`, `tests/rag/{test_index,test_retrieval,test_answer}.py` — atrapowe adaptery do testów offline.

## Task 1: Zweryfikuj `sqlite-vec` i formaty wejściowe na Windows

**Files:**
- Create: `tests/rag/test_sqlite_vec_compatibility.py`
- Create: `tools/check_local_ai.py`
- Modify: `pyproject.toml`, `README.md`

- [ ] **Step 1: Napisz test techniczny wymaganych cech indeksu**

```python
# tests/rag/test_sqlite_vec_compatibility.py
import sqlite3

import sqlite_vec


def test_vec0_supports_per_user_partitioned_knn():
    connection = sqlite3.connect(":memory:")
    sqlite_vec.load(connection)
    connection.execute(
        "CREATE VIRTUAL TABLE chunks USING vec0("
        "chunk_id TEXT PRIMARY KEY, owner_partition TEXT partition key, "
        "embedding float[3] distance_metric=cosine)"
    )
    connection.execute(
        "INSERT INTO chunks(chunk_id, owner_partition, embedding) VALUES "
        "('own', 'owner-a', '[1, 0, 0]'), ('other', 'owner-b', '[1, 0, 0]')"
    )

    rows = connection.execute(
        "SELECT chunk_id FROM chunks WHERE embedding MATCH ? "
        "AND k = 10 AND owner_partition = ?",
        ("[1, 0, 0]", "owner-a"),
    ).fetchall()
    assert rows == [("own",)]
```

- [ ] **Step 2: Uruchom test na docelowym Pythonie**

Run: `py -3.12 -m pytest tests/rag/test_sqlite_vec_compatibility.py -q`
Expected: PASS na wspieranym `sqlite-vec`. Jeśli extension nie instaluje się lub partycja nie jest filtrowana w samym zapytaniu KNN, zatrzymaj ten plan i ustal bezpieczny wariant przechowywania przed zapisywaniem dokumentów.

- [ ] **Step 3: Dodaj narzędzie diagnostyczne bez wysyłania danych**

`tools/check_local_ai.py` sprawdza wersje Python/SQLite, ładowanie `sqlite-vec`, binarkę Tesseract i obecność polskiego pakietu oraz połączenie z `http://127.0.0.1:<port>/v1/models`. Wyświetla nazwy modeli bez treści medycznych i nie wysyła przykładowych dokumentów. Dodaj zależności `sqlite-vec` oraz `pymupdf` do opcjonalnej grupy instalacyjnej `local-ai`.

- [ ] **Step 4: Zweryfikuj diagnostykę i instalację**

Run: `py -3.12 -m pip install -e ".[dev,local-ai]"; py -3.12 tools/check_local_ai.py`
Expected: każda brakująca zależność jest nazwana konkretnie; brak LM Studio/Tesseract nie jest przedstawiany jako sukces. Zapisz wymagane wersje i konfigurację w README.

- [ ] **Step 5: Zapisz test zgodności i diagnostykę**

Run: `git add pyproject.toml README.md tools/check_local_ai.py tests/rag/test_sqlite_vec_compatibility.py; git commit -m "test: verify local vector search support"`
Expected: commit zawiera sprawdzenie zgodności `sqlite-vec` i narzędzie diagnostyczne.

## Task 2: Import prywatnego dokumentu i bezpieczne przechowywanie

**Files:**
- Create: `src/medical_archive/documents/__init__.py`, `models.py`, `storage.py`, `service.py`, `router.py`
- Create: `migrations/versions/0002_documents.py`
- Modify: `src/medical_archive/main.py`, `src/medical_archive/models/__init__.py`
- Create: `tests/documents/test_upload.py`, `test_access.py`

- [ ] **Step 1: Dodaj failing testy dla uploadu i własności**

```python
# tests/documents/test_upload.py
def test_upload_stores_original_outside_webroot_and_requires_review(
    owner_client, document_repository, data_dir
):
    response = owner_client.post(
        "/documents",
        files={"file": ("blood.pdf", b"%PDF-1.4 test", "application/pdf")},
    )

    assert response.status_code == 202
    document = response.json()
    assert document["status"] == "needs_review"
    stored = document_repository.get_owned(document["id"], owner_client.user_id)
    assert stored.storage_key
    assert (data_dir / "documents" / stored.storage_key).is_file()
    assert "storage_key" not in document


def test_upload_rejects_executable_disguised_as_pdf(owner_client):
    response = owner_client.post(
        "/documents",
        files={"file": ("scan.pdf", b"MZ executable", "application/pdf")},
    )
    assert response.status_code == 415
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/documents/test_upload.py -q`
Expected: FAIL — trasa `/documents` jeszcze nie istnieje.

- [ ] **Step 3: Zaimplementuj walidację uploadu i wewnętrzny storage key**

Model `Document` zawiera UUID, `owner_user_id`, `storage_key` (losowy UUID, nigdy nazwa użytkownika), original filename, MIME, byte size, document_type, document_date, processing_status i created_at. Sprawdzaj limit rozmiaru strumieniowo, sygnaturę PDF/JPEG/PNG na podstawie zawartości i rozszerzenia oraz odrzucaj nieoczekiwany format. Nie buduj ścieżki pliku z nazwy przesłanej przez klienta; zapisuj atomowo do wewnętrznego katalogu `<data_dir>/documents`, poza static/webroot. W razie błędu zapisu/INSERT usuwaj tylko plik tymczasowy utworzony przez to żądanie i zwracaj czytelny HTTP 4xx/5xx bez szczegółów systemu plików.

Trasa upload zwraca `202`, status `needs_review` i publiczne metadane bez storage key. Każdy dostęp sprawdza właściciela lub wspólną politykę `visible_resource_ids` z planu 1; brak logowania kieruje do logowania, a użytkownik bez grantu i administrator dostają 404. Endpoint pobierania nigdy nie montuje katalogu plików jako zasobu statycznego.

- [ ] **Step 4: Uruchom testy uploadu i izolacji**

Run: `py -3.12 -m pytest tests/documents/test_upload.py tests/documents/test_access.py -q`
Expected: prawidłowy PDF zapisuje się prywatnie; zbyt duży plik, podrobiony format i plik obcego użytkownika są odrzucane.

- [ ] **Step 5: Zapisz bezpieczny import dokumentów**

Run: `git add src/medical_archive/documents src/medical_archive/main.py src/medical_archive/models migrations/versions/0002_documents.py tests/documents/test_upload.py tests/documents/test_access.py; git commit -m "feat: add private document uploads"`
Expected: commit zawiera upload, prywatny storage, model i testy ACL.

## Task 3: Ekstrakcja tekstu, OCR i potwierdzanie

**Files:**
- Create: `src/medical_archive/documents/extraction.py`, `metadata.py`, `review.py`
- Create: `migrations/versions/0003_document_text.py`
- Modify: `src/medical_archive/documents/service.py`, `router.py`
- Create: `tests/documents/test_extraction.py`, `test_metadata.py`, `test_approval.py`

- [ ] **Step 1: Dodaj failing testy ekstrakcji, skanu i stanów importu**

```python
# tests/documents/test_approval.py
def test_processing_suggests_type_and_date_without_changing_account_owner(
    owner_client, pending_scan, extractor
):
    extractor.set_pages(["Wynik badania laboratoryjnego 12.10.2026"])

    response = owner_client.post(f"/documents/{pending_scan.id}/process")

    assert response.status_code == 200
    assert response.json()["suggested_document_type"] == "lab_result"
    assert response.json()["suggested_document_date"] == "2026-10-12"
    assert response.json()["owner_user_id"] == pending_scan.owner_user_id


def test_document_is_indexed_only_after_owner_approves_metadata(owner_client, pending_scan, indexer):
    process = owner_client.post(f"/documents/{pending_scan.id}/process")
    assert process.status_code == 200
    assert process.json()["status"] == "needs_review"
    assert not indexer.has_document(owner_id=pending_scan.owner_user_id, document_id=pending_scan.id)

    response = owner_client.post(
        f"/documents/{pending_scan.id}/approve",
        json={"document_type": "lab_result", "document_date": "2026-10-01"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert indexer.has_document(owner_id=pending_scan.owner_user_id, document_id=pending_scan.id)


def test_ocr_failure_keeps_original_and_reports_retryable_error(owner_client, unreadable_scan):
    response = owner_client.post(f"/documents/{unreadable_scan.id}/process")
    assert response.status_code == 422
    assert response.json()["status"] == "ocr_failed"
    assert owner_client.get(f"/documents/{unreadable_scan.id}/file").status_code == 200
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/documents/test_extraction.py tests/documents/test_approval.py -q`
Expected: FAIL — ekstraktor tekstu i przejścia stanu jeszcze nie istnieją.

- [ ] **Step 3: Zdefiniuj interfejs ekstraktora i przejścia stanów**

`TextExtractor.extract(path) -> list[ExtractedPage]` zwraca tekst z numerami stron i metodę ekstrakcji. Odczytuj warstwę tekstową PDF przez PyMuPDF; jeśli strona nie ma tekstu, renderuj ją i wywołuj lokalny Tesseract z `lang="pol"`; obrazy przechodzą ten sam OCR. Brak binarki lub pakietu językowego zgłasza jawny `ExtractionError`, a nie pusty tekst. Zapisuj tekst z numerami stron i `ocr_status`; loguj wyłącznie UUID dokumentu, kod błędu i czas, nigdy treść.

`metadata.py` rozpoznaje tylko nieautorytatywne sugestie typu dokumentu (np. słowa „wynik”, „recepta”, „wypis”) i daty z tekstu OCR; użytkownik może je poprawić. Nie sugeruj konta/osoby na podstawie OCR i nigdy nie zmieniaj `owner_user_id`. Stany dokumentu: `needs_review -> indexing -> ready`, osobno `ocr_failed`/`index_failed`. Tylko właściciel może zobaczyć podgląd, zmienić typ/datę, zatwierdzić i ponowić przetwarzanie. Endpoint process ekstrahuje tekst i pozostawia dokument w `needs_review`; zatwierdzenie zapisuje metadane transakcyjnie, indeksuje i ustawia `ready`. Dodaj widoki Jinja2 listy, podglądu oryginału/tekstu i formularza ręcznej akceptacji.

- [ ] **Step 4: Sprawdź pending, approve, błędy i ponowne przetwarzanie**

Run: `py -3.12 -m pytest tests/documents -q`
Expected: PDF z tekstem zwraca strony; fixture obrazu używa atrapowego adaptera OCR; akceptacja uruchamia indeksowanie; błąd i ponowienie nie gubią oryginału ani niezatwierdzonego tekstu.

- [ ] **Step 5: Zapisz ekstrakcję i zatwierdzany import**

Run: `git add src/medical_archive/documents migrations/versions/0003_document_text.py src/medical_archive/templates tests/documents; git commit -m "feat: add local document text extraction"`
Expected: commit zawiera ekstrakcję lokalną, OCR, podgląd i workflow akceptacji.

## Task 4: Indeksowanie fragmentów z granicą ACL

**Files:**
- Create: `src/medical_archive/rag/__init__.py`, `models.py`, `index.py`, `embeddings.py`, `chunking.py`
- Create: `migrations/versions/0004_vector_index.py`
- Modify: `src/medical_archive/documents/service.py`, `src/medical_archive/auth/grants.py`
- Create: `tests/rag/test_index.py`

- [ ] **Step 1: Dodaj failing testy właściciela, grantu i cofnięcia dostępu**

```python
# tests/rag/test_index.py
def test_chunk_is_indexed_only_in_owner_and_explicit_recipient_partitions(indexer, document, recipient):
    indexer.index_approved_document(document)

    assert indexer.find_in_partition(owner_id=document.owner_user_id, query_vector=[1.0, 0.0]) == [document.id]
    assert indexer.find_in_partition(owner_id=recipient.id, query_vector=[1.0, 0.0]) == []

    grant = grant_document(document, recipient)
    indexer.apply_grant(document, grant)
    assert indexer.find_in_partition(owner_id=recipient.id, query_vector=[1.0, 0.0]) == [document.id]

    revoke_grant(grant)
    indexer.remove_from_partition(owner_id=recipient.id, document_id=document.id)
    assert indexer.find_in_partition(owner_id=recipient.id, query_vector=[1.0, 0.0]) == []
```

- [ ] **Step 2: Potwierdź red**

Run: `py -3.12 -m pytest tests/rag/test_index.py -q`
Expected: FAIL — indeksatora jeszcze nie ma.

- [ ] **Step 3: Zaimplementuj dzielenie na fragmenty, adapter embeddingów i vec0 per użytkownik**

Dziel tekst na granicach stron, a następnie na fragmenty około 500 tokenów z overlap 80; każdy fragment zawiera właściciela, UUID dokumentu i numer strony. `EmbeddingClient.embed(texts)` zwraca wektory stałej długości i waliduje liczbę, wymiar, skończoność oraz brak NaN; adapter wywołuje wyłącznie LM Studio pod `127.0.0.1`. Tabela `vec0` ma kolumnę wektorową i partition key użytkownika; identyfikatory fragmentów łączą się z tabelą metadanych przez opaque ID. Zapytanie retrieval zawsze przekazuje partition użytkownika jako warunek bezpośrednio do operatora KNN.

`migrations/env.py` ładuje rozszerzenie `sqlite-vec` na połączeniu migracyjnym przed upgrade/downgrade tabeli wektorowej; brak rozszerzenia zatrzymuje migrację z czytelnym błędem.

Po udzieleniu grantu skopiuj odnośniki/wektory wyłącznie do partycji odbiorcy; administrator nie uzyskuje dostępu. Po cofnięciu grantu natychmiast usuń wpisy odbiorcy. Reindeksowanie najpierw tworzy i kompletnie zapisuje nowy zestaw chunk IDs, następnie atomowo przełącza dokument na nowy indeks; w razie błędu poprzedni indeks i oryginał pozostają dostępne właścicielowi. Błąd `sqlite-vec` przy starcie zgłaszaj jako błąd konfiguracji, bez fallbacku.

- [ ] **Step 4: Sprawdź filtrację na rzeczywistym rozszerzeniu**

Run: `py -3.12 -m pytest tests/rag/test_sqlite_vec_compatibility.py tests/rag/test_index.py -q`
Expected: test rozszerzenia PASS; zapytanie właściciela/odbiorcy zwraca wyłącznie dozwoloną partycję, a revoke wyklucza jej wiersze z wyszukiwania.

- [ ] **Step 5: Zapisz bezpieczny indeks wektorowy**

Run: `git add src/medical_archive/rag migrations/versions/0004_vector_index.py src/medical_archive/documents/service.py src/medical_archive/auth/grants.py tests/rag/test_index.py tests/rag/test_sqlite_vec_compatibility.py; git commit -m "feat: add user-partitioned vector index"`
Expected: commit zawiera indeks per użytkownik, propagację grantów i test izolacji.

## Task 5: Retrieval, Bielik i weryfikowane cytowania

**Files:**
- Create: `src/medical_archive/rag/retrieval.py`, `prompt.py`, `service.py`, `router.py`
- Modify: `src/medical_archive/main.py`, `src/medical_archive/documents/service.py`
- Create: `src/medical_archive/templates/chat.html`
- Create: `tests/rag/test_retrieval.py`, `test_answer.py`

- [ ] **Step 1: Dodaj failing testy braku danych, cytowań i wycieku**

```python
# tests/rag/test_answer.py
def test_answer_uses_only_authorized_context_and_validated_citations(chat_service, alice, private_sources):
    result = chat_service.ask(alice, "When was my blood test?")

    assert result.answer
    assert result.sources
    assert all(source.owner_id == alice.id for source in result.sources)
    assert "[doc:" not in result.answer


def test_no_relevant_source_returns_explicit_no_data_without_calling_generator(
    chat_service, generator, alice
):
    result = chat_service.ask(alice, "What is my diagnosis?")

    assert result.answer == "Nie znalazłem w dostępnych źródłach informacji, która odpowiada na to pytanie."
    assert result.sources == []
    assert generator.calls == 0
```

- [ ] **Step 2: Uruchom test i potwierdź brak serwisu**

Run: `py -3.12 -m pytest tests/rag/test_retrieval.py tests/rag/test_answer.py -q`
Expected: FAIL — handler asystenta nie istnieje.

- [ ] **Step 3: Zaimplementuj retrieval z ACL przed modelem**

`retrieval.py` otrzymuje aktualnego user ID, tworzy embeddings pytania przez model embeddingowy i wyszukuje wyłącznie w jego `vec0` partition. Dopiero zwrócone opaque chunk IDs są mapowane na metadata i ponownie sprawdzane przez `visible_resource_ids`; jakikolwiek chunk spoza ACL jest odrzucany i nie trafia do promptu. Ustal minimalny score/relevance threshold w konfiguracji i zwracaj no-data bez generacji poniżej progu.

`prompt.py` nadaje dokumentom rolę niezaufanego cytowanego materiału, wymaga odpowiedzi po polsku tylko z kontekstu albo jawnego braku źródeł, zabrania diagnozy/zmian leków/leczenia i nakazuje zwracać identyfikatory cytowań. `service.py` weryfikuje zwrócone ID cytowań jako podzbiór źródeł dostarczonych promptowi; wyświetlane źródło jest budowane z DB przez aplikację, nie z tekstu modelu. Nieważny JSON, obce ID, brak modelu lub timeout dają jawny błąd, a nie odpowiedź zastępczą.

`LMStudioClient` ma konfigurowalne loopback URL/model names, krótki timeout dla embeddingów i konfigurowalny timeout generacji; odrzuca URL spoza localhost, wyłącza retry dla generacji, nie loguje promptów, loguje tylko typ błędu/latencję/ID request. Umożliwiaj wstrzyknięcie atrap w testach.

- [ ] **Step 4: Uruchom testy asystenta i pełny zestaw**

Run: `py -3.12 -m pytest tests/rag tests/documents -q`
Expected: cytowanie otwiera stronę źródłową; obce ID jest odrzucone; no-data nie wywołuje Bielika; brak modelu wyświetla błąd.

- [ ] **Step 5: Manualnie sprawdź LM Studio na materiałach przykładowych**

Run: `py -3.12 tools/check_local_ai.py; py -3.12 -m pytest -q; py -3.12 -m ruff check .`
Expected: diagnostyka potwierdza embedding model oraz Bielika lokalnie; testy przechodzą; weryfikacja przykładowym, nieidentyfikującym PDF-em zapisuje trafność retrieval i czas odpowiedzi bez zachowania tekstu/treści w logach.

- [ ] **Step 6: Zapisz asystenta z weryfikowanymi cytowaniami**

Run: `git add src/medical_archive/rag src/medical_archive/main.py src/medical_archive/documents/service.py src/medical_archive/templates/chat.html tests/rag; git commit -m "feat: add source-grounded local assistant"`
Expected: commit zawiera ACL retrieval, adapter LM Studio, kontrolę cytowań i testy asystenta.
