# Plany implementacji — Medyczne Archiwum

Plany są uporządkowane zależnościami. Każdy kończy się możliwą do uruchomienia i przetestowania częścią produktu:

1. `2026-10-13-fundament-i-konta.md` — uruchamialna aplikacja, trwała baza, logowanie, zaproszenia i izolacja danych. Wymagany przed pozostałymi planami.
2. `2026-10-13-dokumenty-ocr-rag.md` — prywatne archiwum, import, OCR, indeksowanie i odpowiedzi lokalnego asystenta. Wymaga planu 1.
3. `2026-10-13-pomiary-i-pulpit.md` — ręczne pomiary, wykres/pulpit oraz wyszukiwanie pomiarów przez istniejącego asystenta. Wymaga planów 1 i 2.
4. `2026-10-13-backup-i-lan.md` — szyfrowany backup/odtworzenie, HTTPS w LAN i końcowe kryteria wydania. Wymaga planów 1–3.

Każdy plan rozpoczyna się od testu, który ma najpierw zakończyć się niepowodzeniem. Komendy zakładają PowerShell, Windows i Python 3.12. W przypadku niepowodzenia przy instalacji bibliotek natywnych SQLite/OCR należy naprawić środowisko, nie usuwać wymaganych testów ani cicho zastępować komponentu.

## Wspólny układ kodu

```text
medyczne-archiwum/
├── pyproject.toml
├── README.md
├── .gitignore
├── alembic.ini
├── migrations/
│   ├── env.py
│   └── versions/
├── src/
│   └── medical_archive/
│       ├── __init__.py
│       ├── main.py
│       ├── config.py
│       ├── db.py
│       ├── models/
│       ├── web/
│       ├── auth/
│       ├── documents/
│       ├── rag/
│       ├── measurements/
│       ├── backup/
│       ├── templates/
│       └── static/
└── tests/
    ├── conftest.py
    ├── auth/
    ├── documents/
    ├── rag/
    ├── measurements/
    └── backup/
```

Moduły domenowe wystawiają serwisy/routery; tylko `web` składa je w aplikację. Zależności infrastrukturalne są wstrzykiwane, aby testy nie wymagały działającego LM Studio ani rzeczywistego OCR. SQLite przechowuje dane strukturalne. Pliki oryginalne pozostają poza katalogiem statycznym. Wyszukiwanie wektorowe korzysta z `sqlite-vec` i osobnego segmentu indeksu przypisanego do użytkownika; rekordy udostępnione jawnie są dodawane do segmentu odbiorcy. Dzięki temu wyszukiwanie zaczyna się w granicy dostępu, a odebranie udostępnienia usuwa odpowiednie wpisy indeksu.

## Wspólne decyzje wykonawcze

- Python 3.12; FastAPI, SQLAlchemy 2, Alembic, SQLite, Jinja2, pytest, HTTPX i Ruff.
- Uwierzytelnianie: hasła Argon2id (`argon2-cffi`), losowy token sesji w ciasteczku `HttpOnly`, `Secure`, `SameSite=Strict`; w bazie przechowywany tylko skrót tokenu. Tokeny zaproszeń są losowe, przechowywane jako skrót, jednorazowe i ograniczone czasowo.
- Administrator nie przegląda danych innych osób w zwykłym UI/API ani przez RAG, ale zgodnie z decyzją użytkownika może eksportować i odtwarzać pełny backup magazynu. Administrator znający hasło backupu może z niego odczytać dane wszystkich profili; dokumentacja i UI muszą pokazać to ostrzeżenie przed eksportem/restore.
- Konfiguracja lokalna walidowana przy starcie. Brak sekretów/domniemanych kluczy domyślnych w repozytorium; administrator jest tworzony przez jawne polecenie inicjalizacyjne.
- Przy każdym endpointcie dostęp do danych jest sprawdzany w serwisie domenowym. Ukrycie przycisku w UI nie zastępuje autoryzacji.
- Dane dokumentów, OCR, pomiarów i zapytań nie trafiają do logów. Wyjątki zewnętrznych narzędzi są zamieniane na jawne, bezpieczne komunikaty UI.
- Wszystkie testy można uruchomić lokalnie bez LM Studio; adaptery modelu/OCR otrzymują atrapę. Test akceptacyjny na docelowym komputerze jest osobnym krokiem.
- Zależność `sqlite-vec`, ograniczenia indeksu `vec0` dla segmentów użytkownika oraz kompatybilność Windows należy potwierdzić małym testem technicznym przed przyjęciem implementacji RAG. Jeśli dany wariant nie potrafi filtrować wektorów po segmencie w zapytaniu KNN, zatrzymać realizację i wrócić do projektu izolacji zamiast filtrować cudze wyniki dopiero po pobraniu.
