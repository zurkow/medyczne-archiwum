# UI Mockups: Kopia zapasowa (backup / restore admin page)

**Generated**: 2026-10-14
**Task Path**: `.maister/tasks/development/2026-10-14-backup-tls-lan`
**Feature Type**: New feature (one new admin page, a nav link, a maintenance page and a login notice)

---

## Overview

### UI requirements
- A new admin-only page `GET /settings/backup` (`templates/backup.html`). It extends `base.html` and contains two forms:
  - **Export**: `POST /settings/backup/export`. The response is the file download.
  - **Restore**: `POST /settings/backup/restore`, sent as `multipart/form-data`.
- A nav link "Kopia zapasowa" in `base.html`, next to "Zaproszenia". It is shown only to admins.
- Polish error messages with `role="alert"`, tied to the field that caused them.
- A 503 maintenance page that every route except `/health` shows while a restore runs.
- After a restore, a redirect to the login page with a success notice.
- An optional info box with a reminder to back up. It never shows record data.

### Integration strategy
**Decision**: `backup.html` uses the same layout as `measurements.html`. It is one `section.panel` with an `h1`, followed by sibling `<section aria-labelledby>` blocks, each with an `h2`. The two forms sit one below the other in a single column. Export comes first because it is the safe, frequent action. Restore comes last because it is destructive. The nav link goes directly after "Zaproszenia", so both admin links sit together at the end of the nav.

**Rationale**:
- Every authenticated page in the app is one panel with a single column, inside `.page-content` (48rem max). There is no settings sidebar and no tab pattern to reuse.
- Putting restore second, below a visual separator, means the admin reads past the export section first. The restore button never sits next to the export button.
- Native HTML only (`required`, `minlength`, `maxlength`, `accept`). No JavaScript, which matches the project rule that pages work as plain HTML forms.

---

## Existing layout analysis

### Application structure
`base.html` renders the following:
- `header.site-header`: dark navy bar, containing
  - `a.brand`
  - `nav[aria-label="Nawigacja główna"]`
- `main.page-content`, centred, `min(100% - 2rem, 48rem)`, containing
  - `{% block content %}`
  - a "Wyloguj" form that is always rendered below the content

On mobile the header stacks into a column. From 42rem up it becomes a row.

**Key files**
| Role | Path |
|---|---|
| Layout and nav | `src/medical_archive/templates/base.html` (nav links are on lines 14–19; the admin link is on line 18) |
| Page pattern to copy | `src/medical_archive/templates/measurements.html` (a panel with `h2` sections and a form with `<label for>`) |
| Download link pattern | `src/medical_archive/templates/document_review.html` ("Pobierz oryginał") |
| Standalone pre-auth pages | `src/medical_archive/templates/login.html`, `register.html`, `invite.html` (no `app.css` link, `p[role=alert]`) |
| Styles | `src/medical_archive/static/app.css` (332 lines) |
| Renderer | `src/medical_archive/web/templates.py` → `render_template(request, name, status_code=..., **ctx)` |
| Login route | `src/medical_archive/auth/router.py:103` `login_page` (renders `error=None` today) |

### Patterns found
- **Panel page**: `<section class="panel" aria-labelledby="X-heading"><h1 id="X-heading">`. Sub-areas are `<section aria-labelledby>` with an `h2`, which `.panel h2` styles.
- **Forms**: `form` is a grid with `gap: .75rem` and `max-width: 32rem`. Each field is a `label` (bold) directly followed by its `input`. The submit `button` uses `width: fit-content`. Every POST form starts with `<input type="hidden" name="csrf_token">`.
- **Errors**: `[role="alert"]` already has a style: red left border, `#7a1b18` on `#fff1f0`. Errors are server-rendered, and the page is re-rendered with a 4xx status (see `chat.html:15`, `login.html:11`).
- **Buttons**: the primary button is `button`. A secondary variant exists as `button.button-secondary`. There is no danger variant.
- **Section divider**: `.medical-limitation` uses `border-top: 1px solid #c6d0dc` (see measurements).
- **Nav state**: the global `[aria-current="page"]` style makes the link bolder and its underline thicker.
- **Not covered by existing CSS**: checkbox styling, warning callouts, success or status notices, field hints.

---

## Mockup 1: nav link (base.html, MODIFIED)

```
Desktop (≥ 42rem) — .site-header flex row
┌──────────────────────────────────────────────────────────────────────────────────────┐
│ Medyczne Archiwum        Pulpit  Dokumenty  Pomiary  Zapytaj asystenta  Zaproszenia   │
│ └ a.brand                                                                Kopia zapasowa│
│                                                                          ══════════════ │
│                                                     NEW (admin only, aria-current) ┘  │
└──────────────────────────────────────────────────────────────────────────────────────┘
          (nav is flex-wrap, so the last link may wrap to a second line — existing behaviour)

Mobile (< 42rem) — .site-header column
┌──────────────────────────────┐
│ Medyczne Archiwum            │
│ Pulpit  Dokumenty  Pomiary   │
│ Zapytaj asystenta            │
│ Zaproszenia  Kopia zapasowa  │ ← NEW, wraps naturally
└──────────────────────────────┘
```

Target markup for `base.html` line 17–19:
```jinja
{% if user.is_admin %}
  <a href="/auth/invite">Zaproszenia</a>
  <a href="/settings/backup" {% if request.url.path.startswith("/settings") %}aria-current="page"{% endif %}>Kopia zapasowa</a>
{% endif %}
```
- ✅ It reuses the `aria-current` idiom from the other links. No new CSS is needed.
- ✅ It is rendered only inside `{% if user.is_admin %}`. Hiding the link is not access control. The route still has to enforce admin, following `security/access-control.md`.
- Note: "Zaproszenia" still has no `aria-current`. That is out of scope, so leave it unchanged.

---

## Mockup 2: page default state, desktop (≥ 42rem)

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│ header.site-header (EXISTING)    … Zaproszenia  [Kopia zapasowa]                  │
└───────────────────────────────────────────────────────────────────────────────────┘
   main.page-content (EXISTING, max 48rem)
   ┌─────────────────────────────────────────────────────────────────────────────┐
   │ section.panel aria-labelledby="backup-heading"           (EXISTING class)  │
   │                                                                             │
   │  Kopia zapasowa                                     ← h1#backup-heading     │
   │                                                                             │
   │  Kopia obejmuje bazę danych i pliki wszystkich kont oraz profili.           │
   │  Plik jest zaszyfrowany hasłem kopii; pobierasz go na swoje urządzenie.     │
   │                                                                             │
   │  ┌ div.info-box  (OPTIONAL, NEW CSS) ─────────────────────────────────────┐ │
   │  │ Utwórz kopię przed każdą aktualizacją aplikacji. Przechowuj plik      │ │
   │  │ i hasło kopii oddzielnie. Zgubionego hasła kopii nie da się odzyskać. │ │
   │  └───────────────────────────────────────────────────────────────────────┘ │
   │                                                                             │
   │  ── section aria-labelledby="export-heading" ─────────────────────────────  │
   │  Utwórz kopię zapasową                              ← h2#export-heading     │
   │                                                                             │
   │  ┃ div.warning-box (NEW CSS)  id="export-warning"                           │
   │  ┃ Uwaga: kopia zawiera dane wszystkich profili. Każdy, kto ma plik kopii   │
   │  ┃ i zna hasło kopii, może odczytać wszystkie dane. Przechowuj ją w         │
   │  ┃ bezpiecznym miejscu.                                                     │
   │                                                                             │
   │  form action="/settings/backup/export" method="post"                        │
   │       aria-describedby="export-warning"   (EXISTING form grid, 32rem)      │
   │   [hidden csrf_token]                                                       │
   │   Twoje hasło administratora                                                │
   │   ┌──────────────────────────────────────┐  type=password                   │
   │   │ ••••••••                             │  autocomplete=current-password   │
   │   └──────────────────────────────────────┘                                  │
   │   Hasło kopii                                                               │
   │   ┌──────────────────────────────────────┐  type=password minlength=12      │
   │   │                                      │  maxlength=1024                  │
   │   └──────────────────────────────────────┘  autocomplete=new-password       │
   │   p.field-hint#export-password-hint (NEW CSS)                               │
   │   Od 12 do 1024 znaków. Bez tego hasła kopii nie da się odtworzyć.          │
   │   Powtórz hasło kopii                                                       │
   │   ┌──────────────────────────────────────┐  same constraints                │
   │   │                                      │                                  │
   │   └──────────────────────────────────────┘                                  │
   │   div.checkbox-field (NEW CSS)                                              │
   │   [ ] Rozumiem, że kopia zawiera dane wszystkich profili   ← required       │
   │                                                                             │
   │   [ Utwórz i pobierz kopię ]               ← EXISTING primary `button`      │
   │                                                                             │
   │  ── section aria-labelledby="restore-heading"  class="danger-zone" (NEW) ── │
   │  ─────────────────────────────────────────────── border-top separator ───── │
   │  Odtwórz z kopii zapasowej                          ← h2#restore-heading    │
   │                                                                             │
   │  ┃ div.warning-box.warning-box-danger (NEW CSS) id="restore-warning"        │
   │  ┃ Odtworzenie zastąpi obecne dane wszystkich kont i profili danymi         │
   │  ┃ z kopii. Tej operacji nie można cofnąć.                                  │
   │  ┃ • Wszyscy użytkownicy, także Ty, zostaną wylogowani.                     │
   │  ┃ • Konta i hasła logowania będą takie, jak w kopii — zaloguj się          │
   │  ┃   hasłem, które obowiązywało w chwili tworzenia kopii.                   │
   │  ┃ • W czasie odtwarzania aplikacja jest niedostępna dla wszystkich.        │
   │                                                                             │
   │  form action="/settings/backup/restore" method="post"                       │
   │       enctype="multipart/form-data" aria-describedby="restore-warning"      │
   │   [hidden csrf_token]          (hidden field FIRST — streaming parser)      │
   │   Plik kopii (.marchive)                                                    │
   │   ┌──────────────────────────────────────┐  type=file accept=".marchive"    │
   │   │ [Wybierz plik]  Nie wybrano pliku    │  required                        │
   │   └──────────────────────────────────────┘                                  │
   │   p.field-hint#restore-file-hint:  Maksymalnie 1 GiB.                       │
   │   Hasło kopii                                                               │
   │   ┌──────────────────────────────────────┐  type=password autocomplete=off  │
   │   └──────────────────────────────────────┘                                  │
   │   Twoje hasło administratora                                                │
   │   ┌──────────────────────────────────────┐  autocomplete=current-password   │
   │   └──────────────────────────────────────┘                                  │
   │   div.checkbox-field                                                        │
   │   [ ] Rozumiem, że obecne dane zostaną zastąpione          ← required       │
   │                                                                             │
   │   [ Odtwórz z kopii ]                      ← button.button-danger (NEW)     │
   │                                                                             │
   └─────────────────────────────────────────────────────────────────────────────┘
   [ Wyloguj ]      ← EXISTING base.html logout form (unchanged)
```

**Integration points**
- ✅ The page structure is the same as `measurements.html`: one panel, an `h1`, and `h2` sections. Heading order is h1 → h2 → h2, with no skipped levels.
- ✅ Each form starts with a hidden `csrf_token`. In the restore form it must come **before** the file part, because the streaming multipart parser has to check CSRF before it reads 1 GiB. The text-field allow-list also needs `csrf_token`, `backup_password`, `admin_password` and `confirm`.
- ✅ The download starts only as the response to the explicit export POST, sent with `Content-Disposition: attachment; filename="medyczne-archiwum-YYYYMMDD-HHMMSS.marchive"`. The page never redirects to a download and never uses `<meta refresh>`. After a successful download the browser stays on the page, which is why there is no success screen for export.
- ✅ Nothing on the page reads from the user, document or measurement tables. The only context variables are `title`, `csrf_token`, `errors`, `max_backup_size_label` and optionally `last_backup_hint`.
- ✅ Password inputs are never pre-filled with a `value`, not even after an error.

### Exact Polish strings
| Key / element | Text |
|---|---|
| `title` / `h1` | `Kopia zapasowa` |
| intro `p` | `Kopia obejmuje bazę danych i pliki wszystkich kont oraz profili. Plik jest zaszyfrowany hasłem kopii; pobierasz go na swoje urządzenie.` |
| info box | `Utwórz kopię przed każdą aktualizacją aplikacji. Przechowuj plik i hasło kopii oddzielnie. Zgubionego hasła kopii nie da się odzyskać.` |
| export `h2` | `Utwórz kopię zapasową` |
| export warning | `Uwaga: kopia zawiera dane wszystkich profili. Każdy, kto ma plik kopii i zna hasło kopii, może odczytać wszystkie dane. Przechowuj ją w bezpiecznym miejscu.` |
| `export-admin-password` label | `Twoje hasło administratora` |
| `export-backup-password` label | `Hasło kopii` |
| hint | `Od 12 do 1024 znaków. Bez tego hasła kopii nie da się odtworzyć.` |
| `export-backup-password-repeat` label | `Powtórz hasło kopii` |
| `export-confirm` label | `Rozumiem, że kopia zawiera dane wszystkich profili` |
| export submit | `Utwórz i pobierz kopię` |
| restore `h2` | `Odtwórz z kopii zapasowej` |
| restore warning (lead) | `Odtworzenie zastąpi obecne dane wszystkich kont i profili danymi z kopii. Tej operacji nie można cofnąć.` |
| restore warning list | `Wszyscy użytkownicy, także Ty, zostaną wylogowani.` / `Konta i hasła logowania będą takie, jak w kopii — zaloguj się hasłem, które obowiązywało w chwili tworzenia kopii.` / `W czasie odtwarzania aplikacja jest niedostępna dla wszystkich.` |
| `restore-file` label | `Plik kopii (.marchive)` |
| file hint | `Maksymalnie 1 GiB.` (rendered from the configured limit) |
| `restore-backup-password` label | `Hasło kopii` |
| `restore-admin-password` label | `Twoje hasło administratora` |
| `restore-confirm` label | `Rozumiem, że obecne dane zostaną zastąpione` |
| restore submit | `Odtwórz z kopii` |

### Field ids and names
| Form | id | name | Attributes |
|---|---|---|---|
| export | `export-admin-password` | `admin_password` | `type=password required autocomplete=current-password` |
| export | `export-backup-password` | `backup_password` | `type=password required minlength=12 maxlength=1024 autocomplete=new-password aria-describedby=export-password-hint` |
| export | `export-backup-password-repeat` | `backup_password_repeat` | same as above |
| export | `export-confirm` | `confirm` | `type=checkbox value=yes required` |
| restore | `restore-file` | `backup_file` | `type=file accept=".marchive" required aria-describedby=restore-file-hint` |
| restore | `restore-backup-password` | `backup_password` | `type=password required maxlength=1024 autocomplete=off` |
| restore | `restore-admin-password` | `admin_password` | `type=password required autocomplete=current-password` |
| restore | `restore-confirm` | `confirm` | `type=checkbox value=yes required` |

The two forms use different id prefixes, so the shared names (`backup_password`, `admin_password`) never collide on the page. The restore backup password has no `minlength`, because an older backup could in principle have been made under a different policy. The server is the authority.

---

## Mockup 3: default state, mobile (< 42rem, min 20rem)

```
┌──────────────────────────────┐
│ Medyczne Archiwum            │
│ Pulpit Dokumenty Pomiary     │
│ Zapytaj asystenta            │
│ Zaproszenia [Kopia zapasowa] │
└──────────────────────────────┘
 ┌────────────────────────────┐  .panel padding 1.25rem
 │ Kopia zapasowa             │  h1 clamp(1.5rem,5vw,2rem)
 │                            │
 │ Kopia obejmuje bazę danych │
 │ i pliki wszystkich kont…   │
 │ ┌ info-box ──────────────┐ │
 │ │ Utwórz kopię przed     │ │
 │ │ każdą aktualizacją…    │ │
 │ └────────────────────────┘ │
 │ Utwórz kopię zapasową      │  h2
 │ ┃ Uwaga: kopia zawiera     │  warning-box
 │ ┃ dane wszystkich profili… │
 │ Twoje hasło administratora │
 │ ┌────────────────────────┐ │  inputs full width
 │ └────────────────────────┘ │  (grid column)
 │ Hasło kopii                │
 │ ┌────────────────────────┐ │
 │ └────────────────────────┘ │
 │ Od 12 do 1024 znaków…      │
 │ Powtórz hasło kopii        │
 │ ┌────────────────────────┐ │
 │ └────────────────────────┘ │
 │ [ ] Rozumiem, że kopia     │  checkbox-field: box
 │     zawiera dane wszystkich│  top-aligned, text wraps
 │     profili                │  under itself, not the box
 │ [Utwórz i pobierz kopię]   │  button min-height 2.75rem
 │ ────────────────────────── │
 │ Odtwórz z kopii zapasowej  │
 │ ┃ Odtworzenie zastąpi …    │  warning-box-danger
 │ ┃ • Wszyscy użytkownicy…   │
 │ ┃ • Konta i hasła …        │
 │ ┃ • W czasie odtwarzania…  │
 │ Plik kopii (.marchive)     │
 │ [Wybierz plik]             │  file input wraps;
 │ Nie wybrano pliku          │  max-width:100%
 │ Maksymalnie 1 GiB.         │
 │ Hasło kopii                │
 │ ┌────────────────────────┐ │
 │ └────────────────────────┘ │
 │ Twoje hasło administratora │
 │ ┌────────────────────────┐ │
 │ └────────────────────────┘ │
 │ [ ] Rozumiem, że obecne    │
 │     dane zostaną zastąpione│
 │ [Odtwórz z kopii]          │  button-danger
 └────────────────────────────┘
 [Wyloguj]
```
- There is no horizontal scroll at 20rem (`body min-width`). Long Polish words wrap. The file input needs `max-width: 100%`, because Chromium's file control has an intrinsic width that can overflow at 320px.
- Tap targets: the existing `input`/`button` `min-height: 2.75rem` (44px) covers them. For the checkbox, the whole `<label>` is the tap target.

---

## Mockup 4: error states

All errors are server-rendered. The POST returns the page itself with the status code shown in the table. Nothing is a redirect, so the error is visible immediately and assistive tech announces it. Each form gets **one** `role="alert"` summary at the top of that form, which reuses the existing `[role="alert"]` style. The faulty field gets `aria-invalid="true"` and `aria-describedby` pointing at a short `p.field-error` next to it. Only one live region exists per response, so screen readers do not announce several alerts.

```
Export form after "passwords don't match"  (HTTP 400)
 ── Utwórz kopię zapasową ───────────────────────────────────────
 ┃ Uwaga: kopia zawiera dane wszystkich profili …   (warning-box)
 ┌ p role="alert" id="export-error" (EXISTING style) ──────────┐
 ▌ Nie utworzono kopii. Popraw zaznaczone pola.               │
 └─────────────────────────────────────────────────────────────┘
  Twoje hasło administratora
  ┌───────────────────────────┐   ← empty again (never re-filled)
  └───────────────────────────┘
  Hasło kopii
  ┌───────────────────────────┐
  └───────────────────────────┘
  Od 12 do 1024 znaków. …        (hint)
  Powtórz hasło kopii
  ┌═══════════════════════════┐   aria-invalid="true" → red border
  └═══════════════════════════┘   aria-describedby="export-repeat-error"
  p.field-error#export-repeat-error (NEW CSS):
  ✖ Hasła kopii nie są takie same.
  [ ] Rozumiem, że kopia …        (checkbox NOT re-checked)
  [ Utwórz i pobierz kopię ]

Restore form after a 422 (whole-file error, no field marker)
 ── Odtwórz z kopii zapasowej ─────────────────────────────────
 ┃ Odtworzenie zastąpi obecne dane …      (warning-box-danger)
 ┌ p role="alert" id="restore-error" ──────────────────────────┐
 ▌ Nie można odtworzyć kopii: plik jest uszkodzony albo hasło  │
 ▌ jest nieprawidłowe. Obecne dane nie zostały zmienione.      │
 └─────────────────────────────────────────────────────────────┘
  Plik kopii (.marchive)
  [Wybierz plik] Nie wybrano pliku   ← browsers never keep a file
  Maksymalnie 1 GiB.                   selection; the user picks again
  …
```

Error placement: the summary sits at the top of the form that was submitted. The other form renders with no error. When the page is re-rendered, `aria-current` and the scroll position reset to the top. Because the alert is a live region it is announced on page load. For keyboard users, add `id`s so a `#restore-error` fragment can be used in the form `action` (for example `action="/settings/backup/restore#restore-error"`). After the POST, the browser then scrolls the error into view without JavaScript.

### Error message catalogue (exact Polish text)
| Situation | HTTP | Form | Summary (`role="alert"`) | Field marked + `p.field-error` text |
|---|---|---|---|---|
| Wrong admin password | 403 | export / restore | `Nie utworzono kopii. Popraw zaznaczone pola.` / `Nie odtworzono kopii. Obecne dane nie zostały zmienione.` | admin password: `Hasło administratora jest nieprawidłowe.` |
| Backup password too short / too long | 400 | export | `Nie utworzono kopii. Popraw zaznaczone pola.` | `export-backup-password`: `Hasło kopii musi mieć od 12 do 1024 znaków.` |
| Repeat does not match | 400 | export | same | `export-backup-password-repeat`: `Hasła kopii nie są takie same.` |
| Confirmation not ticked (if the browser `required` is bypassed) | 400 | export | same | `export-confirm`: `Zaznacz potwierdzenie, aby utworzyć kopię.` |
| Confirmation not ticked | 400 | restore | `Nie odtworzono kopii. Obecne dane nie zostały zmienione.` | `restore-confirm`: `Zaznacz potwierdzenie, aby odtworzyć kopię.` |
| No file / empty file | 400 | restore | same | `restore-file`: `Wybierz plik kopii (.marchive).` |
| Corrupted file, wrong backup password, bad tag, traversal or manifest mismatch | 422 | restore | `Nie można odtworzyć kopii: plik jest uszkodzony albo hasło jest nieprawidłowe. Obecne dane nie zostały zmienione.` | none (deliberately does not say which, to avoid an oracle) |
| File larger than limit | 413 | restore | `Plik kopii jest za duży (maksymalnie 1 GiB). Obecne dane nie zostały zmienione.` | `restore-file` marked |
| Export would exceed limit | 413 | export | `Nie można utworzyć kopii: dane przekraczają limit 1 GiB.` | none |
| Backup has no active admin | 422 | restore | `Nie można odtworzyć kopii: nie zawiera aktywnego konta administratora. Obecne dane nie zostały zmienione.` | none |
| Newer or unknown schema revision | 422 | restore | `Nie można odtworzyć kopii: została utworzona w nowszej lub nieznanej wersji aplikacji. Zaktualizuj aplikację i spróbuj ponownie. Obecne dane nie zostały zmienione.` | none |
| Lock already held | 409 | either | `Operacja kopii zapasowej już trwa.` | none |
| Swap failed and rolled back | 500 | restore | `Odtwarzanie nie powiodło się i zostało wycofane. Obecne dane nie zostały zmienione.` | none |
| CSRF failure | 403 | either | existing `_csrf_or_forbidden` behaviour (unchanged) | — |

Rules:
- Messages never echo the uploaded filename, internal paths, exception text or any record data.
- The 422 text for "corrupted" and "wrong password" is identical. This matches the `InvalidBackupPassword(InvalidBackup)` contract.
- An export failure never sends a partial download. Errors arrive as `text/html` with the page, never as an attachment.

---

## Mockup 5: maintenance page (503, any route except `/health`)

```
Desktop / mobile (identical, single column, centred text block)
┌──────────────────────────────────────────────────────────────┐
│ (no site header, no nav, no logout — no DB/session access)   │
│                                                              │
│   Trwa odtwarzanie kopii zapasowej…            ← h1          │
│                                                              │
│   Aplikacja jest chwilowo niedostępna. Spróbuj ponownie      │
│   za kilka minut.                                            │
│                                                              │
│   Po zakończeniu wszyscy użytkownicy muszą zalogować się     │
│   ponownie.                                                  │
│                                                              │
│   Odśwież stronę                    ← a href="" (reload)     │
│                                                              │
└──────────────────────────────────────────────────────────────┘
HTTP/1.1 503   Retry-After: 30   Cache-Control: no-store
```

- **New template**: `templates/maintenance.html`. It is a **standalone** document like `login.html` (`<html lang="pl">`, `<main>`, `<h1>`). It must not extend `base.html`, because `base.html` reads `user`, `request.url` and `csrf_token`, and the middleware has none of them while the DB is being swapped.
- **Styles**: the middleware blocks `/static/*` too, so a `<link href="/static/app.css">` would also get a 503. Use one of two options:
  - (a) Recommended: a tiny inline `<style>` with the colours from `app.css`: `#16324f` heading, `#1f2937` text on `#f3f6fa`, and the `system-ui` font stack. This fits the standard's "no external URLs" rule.
  - (b) Let `/static/app.css` through the middleware as well.
- **Auto-refresh**: do not add `<meta http-equiv="refresh">`. A timed refresh fails WCAG 2.2.1 and would hit the login redirect mid-swap. A plain "Odśwież stronę" link does the same job.
- **Content type**: return `text/html` for browser navigations. Non-HTML clients (an `Accept` without `text/html`) can get JSON `{"detail": "Trwa odtwarzanie kopii zapasowej…"}` with the same 503.
- **Restoring admin's browser**: the restore POST is the request that triggered maintenance. It does not get this page. It waits for the swap and then gets the 303 in Mockup 6.

Strings:
- `title`/`h1`: `Trwa odtwarzanie kopii zapasowej…`
- body 1: `Aplikacja jest chwilowo niedostępna. Spróbuj ponownie za kilka minut.`
- body 2: `Po zakończeniu wszyscy użytkownicy muszą zalogować się ponownie.`
- link: `Odśwież stronę`

---

## Mockup 6: after the restore, the login page with a success notice

```
Flow
 [Odtwórz z kopii] ──POST multipart──▶ stage + validate ──▶ maintenance on
       │                                                     swap, health check
       │                                                     wipe ALL sessions
       │                                                     maintenance off
       ◀──── 303 See Other  Location: /auth/login?restored=1
             Set-Cookie: session=; Max-Age=0   (clears the stale cookie)

login.html (EXISTING standalone page, MODIFIED: one optional block)
┌──────────────────────────────────────────────────────────┐
│ Zaloguj się                                    ← h1      │
│ ┌ p role="status" class="notice-success" (NEW CSS) ────┐ │
│ ▌ Kopia została odtworzona. Zaloguj się ponownie.      │ │
│ ▌ Użyj hasła, które obowiązywało w chwili tworzenia    │ │
│ ▌ kopii.                                               │ │
│ └──────────────────────────────────────────────────────┘ │
│ Adres e-mail                                             │
│ ┌──────────────────────────────┐                         │
│ └──────────────────────────────┘                         │
│ Hasło                                                    │
│ ┌──────────────────────────────┐                         │
│ └──────────────────────────────┘                         │
│ [ Zaloguj się ]                                          │
└──────────────────────────────────────────────────────────┘
```

- Use `role="status"` (polite), not `role="alert"`. This is good news, and the `[role="alert"]` CSS is red.
- The notice text is a **fixed server-side string** chosen by a flag (`?restored=1` → `notice="restored"`). The query value is never reflected into the page. Any other value is ignored. `login_page` (`auth/router.py:103`) gains `notice=...` in its `render_template` call. Login errors stay as they are.
- `login.html` currently has no `app.css` link. Either add `<link rel="stylesheet" href="/static/app.css">`, which is harmless and gives the notice its style, or accept that the notice is unstyled like the rest of the page. Its text and semantics make sense either way.
- Notice strings:
  - main: `Kopia została odtworzona. Zaloguj się ponownie.`
  - follow-up sentence: `Użyj hasła, które obowiązywało w chwili tworzenia kopii.`

---

## Mockup 7 (optional): info box with a "last backup" hint

```
 ┌ div.info-box ───────────────────────────────────────────────┐
 │ Ostatnia kopia utworzona przez tę instalację:              │
 │ 2026-10-10 18:22 UTC.                                       │
 │ Utwórz kopię przed każdą aktualizacją aplikacji.            │
 └─────────────────────────────────────────────────────────────┘
   variant when nothing is recorded:
 │ Nie utworzono jeszcze kopii z tej instalacji. Utwórz kopię  │
 │ przed każdą aktualizacją aplikacji.                         │
```
- Only show an install-level timestamp, for example a small file under `data_dir` written when an export succeeds. Never show sizes per profile, record counts, filenames or user emails.
- If the backend does not keep a timestamp, render only the static reminder from Mockup 2. That is the recommended minimum, and it needs no new state.
- Note that the timestamp records when an export **was sent**. It does not prove the file was saved.

---

## Reusable components and classes

### Layout (EXISTING, unchanged)
- `base.html`: header, nav, `main.page-content` and the logout form.
- `.panel`, `h1`, `.panel h2`: page container and headings.
- `.page-content`: 48rem column, mobile-first.

### Form elements (EXISTING)
- `form` grid (`gap .75rem`, `max-width 32rem`), `label` (600 weight), `input` (min-height 2.75rem, border `#66758a`).
- `button`: primary blue, used for "Utwórz i pobierz kopię".
- `:focus-visible` outline `3px #174ea6`: already covers inputs, buttons and links.
- `[role="alert"]`: error summaries.
- `.visually-hidden`: available if a hidden hint is needed, for example "(wymagane)".

### Templates touched
| File | Change |
|---|---|
| `templates/base.html` | MODIFIED: one nav link |
| `templates/backup.html` | NEW: extends `base.html` |
| `templates/maintenance.html` | NEW: standalone, with inline styles |
| `templates/login.html` | MODIFIED: optional `notice` block (and optionally the `app.css` link) |

### New CSS (append to `static/app.css`, kebab-case, rem)
Keep it to these classes. Colour pairs are chosen for ≥ 4.5:1 contrast.

| Class | Purpose | Suggested values |
|---|---|---|
| `.warning-box` | Amber callout for the export warning. It is not a live region. | `padding .75rem; border-left .25rem solid #8a5a00; color #4d3300; background #fff6e0` (about 10:1) |
| `.warning-box-danger` | Stronger red callout for the restore warning. It has the same colours as alert, so it is static but reads as danger. | `border-left-color #a12622; color #7a1b18; background #fff1f0` (about 8:1) |
| `.warning-box ul` | Bullet list inside the callout | `margin: .5rem 0 0; padding-left: 1.25rem` |
| `.info-box` | Neutral reminder | `padding .75rem; border: 1px solid #c6d0dc; border-radius .4rem; background #eaf0f6` (text `#1f2937`, about 13:1) |
| `.checkbox-field` | Checkbox and its label in one row | `display:flex; gap:.6rem; align-items:flex-start` |
| `.checkbox-field input[type="checkbox"]` | Overrides the global `input` min-height and padding | `min-height:auto; width:1.25rem; height:1.25rem; margin-top:.2rem; padding:0; flex:none; accent-color:#174ea6` |
| `input[type="file"]` | Stops overflow at 320px | `max-width:100%; padding:.4rem` |
| `.field-hint` | Help text under a field | `margin:-.4rem 0 0; font-size:.95rem; color:#4b5563` (about 7.5:1 on white) |
| `.field-error` | Error text tied to a field | `margin:-.4rem 0 0; color:#7a1b18; font-weight:600` |
| `input[aria-invalid="true"]` | Marks the faulty field. The ✖ text and `.field-error` mean colour is not the only signal. | `border-color:#a12622; border-width:2px` |
| `button.button-danger` | Restore submit | `background:#a12622; color:#fff` (about 6.6:1); `:hover { background:#7a1b18 }` |
| `.danger-zone` | Separates the restore section | `margin-top:2rem; padding-top:1rem; border-top:1px solid #c6d0dc` (same rule as `.medical-limitation`) |
| `[role="status"].notice-success` | Login success notice | `padding:.75rem; border-left:.25rem solid #1e6b35; color:#14532d; background:#ecf7ef` (about 8:1) |

Roughly 45 lines of CSS. No new colour family other than the amber and green notice colours.

---

## Implementation notes

### Consistency checklist
- ✅ The page `{% extends "base.html" %}` and uses `section.panel[aria-labelledby]` + `h1#backup-heading`, the same as measurements and documents.
- ✅ Each `h2` section is a `<section aria-labelledby="export-heading|restore-heading">`.
- ✅ Every input has a `<label for>`. The checkbox label wraps or follows its input, and the label text is the full sentence.
- ✅ Each form starts with a hidden `csrf_token`. The restore form also sends `enctype="multipart/form-data"`.
- ✅ The page is rendered through `render_template(request, "backup.html", title="Kopia zapasowa", status_code=…)`.
- ✅ No JavaScript. No `<meta refresh>`. No auto-download.
- ✅ No record data or user data anywhere on the page, the maintenance page or the error messages.
- ✅ Visible text and labels are in Polish, and HTML is `lang="pl"`.

### Accessibility
- Each form has `aria-describedby` pointing at its warning box. Screen readers then read the warning when focus enters the form.
- There is one `role="alert"` per response, at the top of the submitted form. Field errors are linked with `aria-describedby` and `aria-invalid`.
- Destructive intent shows in more than colour: a separate section, a red callout with explicit text, a required checkbox and a distinct button label.
- Focus order follows the visual order: warning (not focusable), file, backup password, admin password, checkbox, submit.
- After an error the page reloads at the top. The `#export-error` / `#restore-error` action fragments bring the error into view without JavaScript.
- The restore can take a while. The browser shows its native loading indicator. No custom spinner is needed. Optionally, a static sentence under the button: `Odtwarzanie może potrwać kilka minut. Nie zamykaj tej karty.`
- `required` gives instant native validation. The server repeats every check, with the messages in Mockup 4.

### Responsive behaviour
- **Desktop (≥ 42rem)**: header in a row. Panel padding 2rem. Forms max 32rem wide, left-aligned inside the 48rem panel. Warning boxes span the full panel width.
- **Mobile (< 42rem, down to 20rem)**: header stacks and the nav wraps. Panel padding 1.25rem. Inputs and the file control are full width. Checkbox labels wrap beside the box. Buttons stay at `fit-content`, and their labels fit at 320px.

---

## Alternatives considered

### Option A: two separate pages (`/settings/backup/export`, `/settings/backup/restore`). Rejected.
**Why**: it adds a nav level or sub-menu that the app does not have. One page with clear sections and a danger zone is discoverable, and it keeps the reminder to "make a copy before restoring" next to the restore form.

### Option B: a confirmation interstitial page before the restore ("Czy na pewno?"). Rejected.
**Why**: the 1 GiB upload would have to be staged first and confirmed later. That adds server state and a second password step. The required checkbox, the admin password re-entry and the explicit warnings give the same protection in one request.

### Option C: link from the dashboard (`dashboard.html`) instead of the nav. Rejected as the primary entry.
**Why**: the gap analysis puts admin tools next to "Zaproszenia" in the nav (8/10 discoverability). A dashboard card could be added later as a backup reminder, but it is not needed.

### Option D: single page, export section first, restore danger zone last, nav link next to "Zaproszenia". Selected.
**Why**: it follows the established panel layout, reuses existing form, button and alert styles, needs about 45 lines of CSS and no JavaScript, and keeps the destructive action visually separate and deliberate.

---

*Generated by ui-mockup-generator subagent*
