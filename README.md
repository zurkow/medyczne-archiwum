
# Medyczne Archiwum

Prywatne archiwum medyczne z kontami domowników i jawnymi uprawnieniami do
rekordów. Administrator może zarządzać zaproszeniami, ale nie otrzymuje dostępu
do prywatnych zasobów użytkowników. Aplikacja obsługuje zatwierdzone dokumenty,
ręczne pomiary oraz odpowiedzi asystenta oparte na dostępnych źródłach.

Aplikacja jest przeznaczona do pracy na jednym komputerze z Windows w zaufanej
sieci domowej: przez HTTPS, na jawnym prywatnym adresie LAN, z modelem AI
dostępnym wyłącznie na loopback. Przed zapisaniem prawdziwych danych medycznych
przejdź sekcje 2–5 i wykonaj próbne odtworzenie kopii (sekcja 9).

**Nie umieszczaj w repozytorium prawdziwych danych medycznych, danych
użytkowników, kopii zapasowych, certyfikatów, kluczy ani plików `.env`.**
Wszystkie adresy w tym pliku (`192.168.1.10`, `archiwum.lan`) to przykłady.

## Wymagania

- 64-bitowy Python 3.12 na Windows.
- `sqlite-vec` i `cryptography` (instalowane razem z pakietem).
- Opcjonalnie: PyMuPDF (`local-ai`), Tesseract OCR z językiem `pol`, LM Studio.
- Do pracy w sieci LAN: [mkcert](https://github.com/FiloSottile/mkcert).

## 1. Uruchomienie deweloperskie

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

Aplikacja domyślnie przechowuje bazę SQLite i pliki w katalogu danych
użytkownika (`~\.medical-archive`; inny katalog wskażesz zmienną
`MEDICAL_ARCHIVE_DATA_DIR`, a inną bazę zmienną `MEDICAL_ARCHIVE_DATABASE_URL`).
Utwórz schemat i skonfiguruj pierwsze konto administratora:

```powershell
python -m alembic upgrade head
python -m medical_archive.auth.bootstrap --email admin@example.test
```

Polecenie bootstrap pobiera hasło interaktywnie i może utworzyć wyłącznie
pierwszego administratora. Uruchom serwer w trybie deweloperskim (HTTP tylko
na `127.0.0.1:8000`):

```powershell
python -m medical_archive
```

Strona logowania jest dostępna pod adresem `http://127.0.0.1:8000/auth/login`;
endpoint `GET /health` zwraca `{"status": "ok"}`. Przy pierwszym logowaniu
administrator może utworzyć jednorazowy link zaproszenia dla kolejnego konta.

Przed startem serwer uruchamia kontrolę wstępną. Ten sam raport możesz
wyświetlić ręcznie:

```powershell
python -m medical_archive.preflight
```

Status `ok` lub `degraded` (brak opcjonalnego OCR albo LM Studio) pozwala
pracować; brakujące elementy są wypisane jawnie. Status `failed` oznacza brak
elementu krytycznego (Python 3.12, `sqlite-vec`, baza w wersji `head`,
zapisywalny katalog danych, w produkcji również TLS). W trybie produkcyjnym
serwer odmawia wtedy startu, więc na nowej instalacji najpierw wykonaj
`python -m alembic upgrade head`.

## Testy i kontrole jakości

```powershell
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Pełna bramka wydania (lint, formatowanie, testy, migracje `upgrade head` →
`downgrade base` → `upgrade head` na tymczasowej bazie, kontrola wstępna oraz
sprawdzenie, że `git status` nie pokazuje baz danych, kopii, certyfikatów,
kluczy ani plików `.env`):

```powershell
.\scripts\check-release.ps1
```

Skrypt przerywa działanie na pierwszym nieudanym kroku, podaje jego nazwę i
kończy się kodem 1. Status kontroli wstępnej `degraded` jest tylko
ostrzeżeniem. Na koniec wypisuje `Release checks passed`. Kontrola wstępna
sprawdza bazę wskazaną przez bieżące zmienne `MEDICAL_ARCHIVE_*`, dlatego uruchamiaj
skrypt w tej samej konfiguracji, w której działa aplikacja.

### Zrzuty ekranu (Playwright)

Skrypt uruchamia tymczasowy serwer na loopback z pustą bazą, fikcyjnym kontem
administratora i przykładowymi pomiarami, a następnie zapisuje zrzuty
głównych stron w wersji desktop i mobile:

```powershell
python -m pip install -e ".[dev,screenshots]"
python -m playwright install chromium
python tools\capture_screenshots.py --output screenshots
```

Dodatkowe strony dodasz parametrem `--page /sciezka` (można powtarzać).
Katalog `screenshots/` jest ignorowany przez git.

### GitHub Actions

- **CI** (`.github/workflows/ci.yml`) — przy każdym pull requeście i pushu do
  `main` uruchamia `ruff check`, `ruff format --check`, `pytest` i zrzuty
  Playwright. W pull requeście publikuje zrzuty na gałęzi `ci-screenshots` i
  dodaje (lub aktualizuje) komentarz z wynikami i podglądem stron.
- **Claude** (`.github/workflows/claude.yml`) — oznaczenie issue etykietą
  `claude` uruchamia Claude Code, który implementuje zmianę, uruchamia testy i
  zrzuty, a następnie otwiera pull request. Komentarz z `@claude` w issue lub
  pull requeście (od właściciela lub współpracownika) zleca poprawki.

Jednorazowa konfiguracja repozytorium:

1. Zainstaluj aplikację [Claude GitHub App](https://github.com/apps/claude) w
   repozytorium.
2. Wygeneruj token poleceniem `claude setup-token` i zapisz go jako sekret
   repozytorium `CLAUDE_CODE_OAUTH_TOKEN` (Settings → Secrets and variables →
   Actions).
3. Utwórz etykietę `claude` (Issues → Labels).
4. W Settings → Actions → General ustaw „Workflow permissions” na „Read and
   write permissions” (potrzebne do publikacji zrzutów i komentarza).

Zrzuty i treści issue/PR nie mogą zawierać prawdziwych danych medycznych.

## 2. Uruchomienie w sieci LAN (HTTPS)

W sieci LAN aplikacja działa wyłącznie przez HTTPS, na jawnym prywatnym adresie
IP komputera. Tryb produkcyjny odrzuca adresy `0.0.0.0`, `::`, loopback,
link-local, adresy publiczne i CGNAT, brak certyfikatu, brak klucza oraz brak
sekretu sesji, a komunikat błędu wymienia każde błędne pole.

**Certyfikat (mkcert).** Na komputerze z aplikacją utwórz lokalny urząd
certyfikacji i certyfikat dla adresu IP i nazwy, pod którymi aplikacja będzie
dostępna:

```powershell
mkcert -install
mkcert 192.168.1.10 archiwum.lan
```

Drugie polecenie tworzy w bieżącym katalogu plik certyfikatu
`192.168.1.10+1.pem` i klucz `192.168.1.10+1-key.pem`. Skrypty nigdy nie
generują certyfikatów.

**Przechowywanie klucza.** Przenieś oba pliki poza repozytorium, np. do
`C:\certs\`, i ogranicz dostęp do klucza do własnego konta:

```powershell
icacls C:\certs\192.168.1.10+1-key.pem /inheritance:r /grant:r "$($env:USERNAME):(R)"
```

Nie kopiuj klucza ani pliku `rootCA-key.pem` z katalogu mkcert na inne
urządzenia i nie wysyłaj ich nikomu.

**Instalacja certyfikatu CA na klientach.** Katalog z certyfikatem głównym
pokaże polecenie `mkcert -CAROOT`. Na inne urządzenia przenoś wyłącznie plik
`rootCA.pem` (certyfikat publiczny):

- **Windows** (inny komputer): skopiuj plik jako `rootCA.crt` i zaimportuj go
  do magazynu „Zaufane główne urzędy certyfikacji”, np.
  `Import-Certificate -FilePath .\rootCA.crt -CertStoreLocation Cert:\CurrentUser\Root`.
- **Android**: Ustawienia → Zabezpieczenia → Szyfrowanie i dane logowania →
  Zainstaluj certyfikat → Certyfikat CA (nazwy menu zależą od producenta).
- **iOS / iPadOS**: przenieś plik na urządzenie (np. AirDrop), zainstaluj
  pobrany profil w Ustawieniach, a następnie włącz pełne zaufanie w Ustawienia →
  Ogólne → To urządzenie → Ustawienia zaufania certyfikatów.

Nazwa `archiwum.lan` działa tylko wtedy, gdy rozwiązuje ją DNS routera albo plik
`hosts` klienta. Bez tego używaj adresu `https://192.168.1.10:8443`.

**Zmienne produkcyjne.** Skrypt `run-lan.ps1` sam ustawia
`MEDICAL_ARCHIVE_ENVIRONMENT=production`, `MEDICAL_ARCHIVE_HOST`,
`MEDICAL_ARCHIVE_PORT`, `MEDICAL_ARCHIVE_TLS_CERT` i `MEDICAL_ARCHIVE_TLS_KEY`
na podstawie parametrów. Przed uruchomieniem ustaw sekret sesji (sekcja 1) oraz
dodatkowe nazwy hosta, pod którymi klienci otwierają aplikację (adres IP z
`-HostAddress` jest dozwolony automatycznie; symbole wieloznaczne są
odrzucane):

```powershell
$env:MEDICAL_ARCHIVE_ALLOWED_HOSTS = '["archiwum.lan"]'
```

Uruchomienie:

```powershell
.\scripts\run-lan.ps1 -HostAddress 192.168.1.10 -Port 8443 -CertFile C:\certs\192.168.1.10+1.pem -KeyFile C:\certs\192.168.1.10+1-key.pem
```

Skrypt kończy się polskim komunikatem, gdy brakuje certyfikatu, klucza lub
zmiennej `MEDICAL_ARCHIVE_SESSION_SECRET`. Ostateczną walidację wykonuje
konfiguracja aplikacji.

## 3. Zapora Windows

Zezwól na ruch przychodzący do portu aplikacji wyłącznie w profilu
**Prywatnym** i tylko z lokalnej podsieci (PowerShell jako administrator):

```powershell
New-NetFirewallRule -DisplayName "Medyczne Archiwum HTTPS (LAN)" -Direction Inbound -Protocol TCP -LocalPort 8443 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

Sprawdź, że sieć domowa ma kategorię `Private` (`Get-NetConnectionProfile`) i że
żadna reguła nie otwiera tego portu w profilu Publicznym ani Domenowym:

```powershell
Get-NetFirewallPortFilter | Where-Object LocalPort -eq 8443 | Get-NetFirewallRule | Select-Object DisplayName, Enabled, Profile, Direction, Action
```

Nie twórz reguły dla portu LM Studio.

## 4. LM Studio tylko na localhost

Serwer API LM Studio musi nasłuchiwać tylko na `127.0.0.1` (wyłącz w LM Studio
opcję udostępniania w sieci lokalnej). Aplikacja odrzuca adres
`MEDICAL_ARCHIVE_LM_STUDIO_BASE_URL` spoza loopback (`127.0.0.1`, `localhost`,
`::1`); domyślnie jest to `http://127.0.0.1:1234/v1`. Sprawdź adres
nasłuchiwania:

```powershell
Get-NetTCPConnection -LocalPort 1234 -State Listen | Select-Object LocalAddress, LocalPort, OwningProcess
netstat -ano | findstr ":1234"
```

Poprawny wynik zawiera wyłącznie `127.0.0.1` (lub `::1`). Adres `0.0.0.0`, `::`
albo adres LAN oznacza, że model jest dostępny z sieci — popraw ustawienia LM
Studio przed dalszą pracą.

## 5. Kopia zapasowa

Kopię tworzy administrator na stronie **Kopia zapasowa** (`/settings/backup`).

- **Zakres.** Kopia obejmuje bazę danych i pliki wszystkich kont oraz profili,
  także te, do których administrator normalnie nie ma dostępu. Każdy, kto ma plik
  kopii i zna hasło kopii, może odczytać wszystkie dane.
- **Hasło kopii** ma od 12 do 1024 znaków i nie jest hasłem logowania. Plik jest
  szyfrowany (AES-256-GCM, klucz z hasła przez scrypt). Zgubionego hasła kopii
  nie da się odzyskać.
- **Przechowuj plik i hasło kopii oddzielnie**, np. plik na dysku zewnętrznym,
  hasło w menedżerze haseł. Nie trzymaj kopii w repozytorium.
- Do utworzenia kopii potrzebne są: hasło administratora, dwukrotnie wpisane
  hasło kopii i potwierdzenie zakresu. Maksymalny rozmiar kopii to 1 GiB.
- **Zawsze utwórz kopię przed aktualizacją aplikacji lub migracją bazy.**

## 6. Aktualizacja aplikacji

1. Utwórz kopię zapasową (sekcja 5).
2. Zatrzymaj serwer (Ctrl+C w oknie, w którym działa).
3. Pobierz zmiany i zaktualizuj zależności:
   ```powershell
   git pull
   python -m pip install -e ".[dev,local-ai]"
   ```
4. Zaktualizuj schemat bazy: `python -m alembic upgrade head`.
5. Uruchom bramkę wydania: `.\scripts\check-release.ps1` (musi zakończyć się
   `Release checks passed`).
6. Uruchom serwer ponownie (`python -m medical_archive` lub
   `.\scripts\run-lan.ps1 …`).

## 7. Odtwarzanie przez UI

1. Zaloguj się jako administrator i otwórz **Kopia zapasowa**.
2. W sekcji **Odtwórz z kopii zapasowej** wybierz plik `.marchive`, wpisz hasło
   kopii, swoje hasło administratora i zaznacz potwierdzenie.
3. Kliknij **Odtwórz z kopii** i nie zamykaj karty.

Co się dzieje:

- Plik jest najpierw w całości odszyfrowany i sprawdzony w osobnym katalogu
  roboczym. Uszkodzony plik, błędne hasło, kopia z nowszej wersji aplikacji lub
  kopia bez konta administratora są odrzucane, a obecne dane pozostają bez
  zmian. Kopia ze starszą wersją schematu jest aktualizowana na kopii roboczej.
- Na czas podmiany aplikacja przechodzi w tryb konserwacji i jest niedostępna
  dla wszystkich.
- Po odtworzeniu **wszyscy użytkownicy są wylogowani**. Konta i hasła są takie,
  jak w chwili tworzenia kopii — zaloguj się hasłem, które wtedy obowiązywało.
- Jeśli podmiana się nie powiedzie, poprzednie dane są automatycznie
  przywracane.

## 8. Odtwarzanie offline (CLI)

Gdy interfejs WWW jest niedostępny:

1. Zatrzymaj serwer. Polecenie nie sprawdza samo, czy serwer działa — odtwarzanie
   przy uruchomionym serwerze podmieni bazę pod działającą aplikacją.
   Na nowym komputerze (pusty katalog danych) najpierw wykonaj
   `python -m alembic upgrade head`; bez istniejącej bazy odtwarzanie zostanie
   przerwane.
2. Uruchom (w tej samej konfiguracji `MEDICAL_ARCHIVE_*`, w której działa
   aplikacja):
   ```powershell
   .\scripts\restore-backup.ps1 -BackupFile D:\kopie\archiwum.marchive
   ```
   Polecenie ostrzega, że wszystkie dane i sesje zostaną zastąpione, wymaga
   wpisania `RESTORE`, a następnie pyta o hasło kopii. Obowiązują te same
   kontrole co w UI; po sukcesie wypisuje
   `Restore completed. All sessions were signed out.`
3. Uruchom serwer.

Jeśli odtwarzanie zostało przerwane (np. awaria zasilania), aplikacja i
polecenie odtwarzania przy następnym starcie automatycznie dokończą sprzątanie
albo wycofają zmiany. Jeśli start mimo to odmawia działania, **nie zmieniaj
katalogu danych** (nie usuwaj z niego plików) i skontaktuj się z opiekunem
instalacji.

## 9. Weryfikacja kopii

Regularnie (i przed pierwszym zapisaniem prawdziwych danych) sprawdzaj, czy
kopię da się odtworzyć — na osobnym katalogu danych, nigdy na danych
produkcyjnych:

1. W nowym oknie PowerShell wskaż pusty katalog testowy:
   ```powershell
   $env:MEDICAL_ARCHIVE_DATA_DIR = "C:\archiwum-test"
   $env:MEDICAL_ARCHIVE_DATABASE_URL = "sqlite:///C:/archiwum-test/medical_archive.db"
   python -m alembic upgrade head
   python -m medical_archive.auth.bootstrap --email admin@example.test
   ```
2. Uruchom serwer deweloperski, dodaj dane syntetyczne (np. pomiar testowy),
   utwórz kopię, a potem zmień dane (dodaj kolejny pomiar).
3. Odtwórz kopię przez UI albo `.\scripts\restore-backup.ps1` i sprawdź, że
   widać stan z chwili utworzenia kopii, a sesje zostały zakończone.
4. Zamknij okno i usuń katalog testowy.

Aby sprawdzić prawdziwą kopię, odtwórz ją w ten sam sposób na katalogu
testowym, a potem usuń ten katalog.

## 10. Utrata hasła kopii lub hasła administratora

- **Hasło kopii**: nie da się go odzyskać ani obejść. Plik kopii bez hasła jest
  bezużyteczny — utwórz nową kopię i zapisz hasło.
- **Hasło administratora**: aplikacja nie ma polecenia resetu hasła. Polecenie
  bootstrap tworzy administratora tylko w bazie bez administratora (nowa
  instalacja lub nowy katalog danych). Na istniejącej instalacji możesz odtworzyć
  starszą kopię, dla której znasz hasło administratora (offline, sekcja 8);
  zmiany wprowadzone po jej utworzeniu zostaną utracone.

## 11. Kontrola izolacji sieci

Po każdej zmianie sieci, zapory lub LM Studio:

- telefon w sieci LAN otwiera `https://archiwum.lan:8443` (lub
  `https://192.168.1.10:8443`) bez ostrzeżenia o certyfikacie;
- spoza sieci LAN (np. telefon na danych komórkowych) port aplikacji jest
  zamknięty; router nie ma przekierowania portu do tego komputera;
- port LM Studio (`1234`) jest zamknięty na każdym interfejsie poza loopback
  (sekcja 4), także z telefonu w sieci LAN.

## 12. Ograniczenia

- Windows nie zapewnia bezpiecznego usuwania plików. Podczas tworzenia i
  odtwarzania kopii niezaszyfrowane pliki tymczasowe przez krótki czas znajdują
  się w `data_dir\.backup-tmp` i `data_dir\.restore-staging`; są usuwane po
  operacji, ale mogą pozostać odzyskiwalne na dysku.
- Zalecane jest szyfrowanie dysku BitLocker.
- Maksymalny rozmiar kopii to 1 GiB.
- Odtwarzanie wymaga wolnego miejsca na dysku około 3 razy większego niż plik
  kopii.
- Eksport i odtwarzanie zużywają chwilowo pamięć RAM około 2–3 razy większą niż
  plik kopii (dla kopii 1 GiB — do około 3 GiB).
- Ścieżka katalogu danych powinna być krótka (np. `C:rchiwum`). Pliki
  tymczasowe kopii dodają do niej około 110 znaków, a Windows domyślnie
  ogranicza ścieżki do 260 znaków.

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
`pol.traineddata`. Dodaj katalog binarny Tesseract do `PATH` (aplikacja szuka
programu `tesseract` wyłącznie w `PATH`). Gdy pliki danych językowych nie są
wykrywane automatycznie, ustaw `TESSDATA_PREFIX` na katalog zawierający te
pliki.

Do diagnostyki LM Studio uruchom lokalny serwer API na loopback (sekcja 4) i
załaduj modele. Adres serwera ustawia zmienna
`MEDICAL_ARCHIVE_LM_STUDIO_BASE_URL` (domyślnie `http://127.0.0.1:1234/v1`).
Pełny raport, w którym brak każdego opcjonalnego elementu kończy narzędzie kodem
1:

```powershell
python -m medical_archive.preflight --strict
```

(`py -3.12 tools/check_local_ai.py` wykonuje to samo.) Raport sprawdza wersję
Pythona, załadowanie `sqlite-vec`, bazę danych, katalog danych, PyMuPDF,
Tesseract wraz z `pol.traineddata` oraz identyfikatory modeli z
`<MEDICAL_ARCHIVE_LM_STUDIO_BASE_URL>/models`. Wysyła wyłącznie lokalne żądanie
HTTP GET bez treści dokumentu. Brak Tesseracta, danych polskiego OCR, LM Studio
lub załadowanego modelu jest jawnie oznaczany jako `MISSING`; nie jest
przedstawiany jako udana konfiguracja.

Indeksowanie wysyła tekst wyłącznie do lokalnego endpointu embeddingów LM Studio
(`MEDICAL_ARCHIVE_LM_STUDIO_BASE_URL`). Użyj modelu embeddingów zgodnego z
wymiarem 768; domyślny identyfikator `text-embedding-nomic-embed-text-v1.5`
odpowiada modelowi Nomic załadowanemu w LM Studio. Identyfikator chatu Bielik to
`bielik-1.5b-v3.0-instruct`. Obie nazwy można nadpisać zmiennymi
`MEDICAL_ARCHIVE_EMBEDDING_MODEL` i `MEDICAL_ARCHIVE_CHAT_MODEL`. Aplikacja
odrzuca endpoint spoza loopback oraz nie zapisuje treści dokumentu ani wektorów
w logach.

Fragmentowanie nie pobiera ani nie wysyła tekstu do zewnętrznego tokenizera.
Ponieważ tokenizer modelu nie jest częścią lokalnych zależności, używa
deterministycznego lokalnego przybliżenia: ciągi Unicode złożone z liter/cyfr
dzieli na jednostki po maksymalnie cztery znaki, a każdą interpunkcję liczy jako
jedną jednostkę. To jawne przybliżenie, a nie deklaracja zgodności z tokenizerem
modelu. Fragmenty mają limit 500 takich jednostek i overlap 80; granice stron
pozostają zachowane.
