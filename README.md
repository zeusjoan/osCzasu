# Oś Czasu

Prywatna przeglądarka historii lokalizacji z Google Timeline. Wczytujesz pliki `Timeline.json` kilku osób, a potem oglądasz dzień po dniu na mapie: trasy (samochód, rower, pieszo…) i odwiedzone miejsca z nazwami z OpenStreetMap. Możesz też szukać, kiedy i jak długo byłeś w danym sklepie.

Działa jako jeden kontener Docker w sieci domowej, **bez logowania**. Nie wystawiaj go do internetu.

## Uruchomienie

```bash
OSM_CONTACT=twoj@email.pl docker compose up -d --build
```

Potem otwórz `http://<adres-serwera>:8080`.

`OSM_CONTACT` trafia do nagłówka `User-Agent` zapytań do OpenStreetMap. Regulamin ich darmowych serwerów prosi o kontakt do autora zapytań.

## Skąd wziąć plik

Na telefonie z Androidem: **Ustawienia → Lokalizacja → Usługi lokalizacyjne → Oś czasu → Eksportuj dane osi czasu**. Na iPhonie: aplikacja Mapy Google → **Twoja oś czasu → ⋯ → Ustawienia → Eksportuj oś czasu**. Wynikiem jest plik `Timeline.json`.

Obsługiwany jest tylko ten nowy format, eksportowany z telefonu. Stary eksport z Google Takeout (`Records.json`) nie jest obsługiwany.

## Jak to działa

- **Osoby i pliki:** każdy plik przypisujesz do osoby. Jedna osoba może mieć kilka plików. Nowszy plik zastępuje wpisy starszych plików w okresie, który obejmuje, więc nic się nie dubluje.
- **Nazwy miejsc:** pobierane w tle z OpenStreetMap, około 1 miejsca na sekundę. Pierwsze wczytanie dużego pliku zajmuje kilkanaście minut, a postęp widać u góry („Nazwy miejsc: 312/733”). Każde miejsce jest pytane tylko raz. Jeśli serwer OSM jest przeciążony, aplikacja ponawia zapytania później. Można też ustawić zapasowy serwer w `OVERPASS_URL` (patrz `docker-compose.yml`).
- **Transport:** etykieta pochodzi od Google. Kliknij przejazd dwa razy, żeby ją poprawić.
- **Wyszukiwanie:** po nazwie lub kategorii (np. „lidl”, „apteka”), bez znaczenia wielkie litery i polskie znaki. Wizyty, przy których szukany lokal był tylko w pobliżu, są oznaczone jako „możliwie tutaj”.

## Dane i kopia zapasowa

Wszystko jest w katalogu `data/` obok `docker-compose.yml`: baza `oscz.db` i wgrane pliki w `uploads/`. Kopia zapasowa to skopiowanie tego katalogu (najlepiej przy zatrzymanym kontenerze).

Poza Twój serwer wychodzą tylko współrzędne odwiedzonych miejsc (do OpenStreetMap, po nazwy) oraz zapytania o kafelki mapy.

## Rozwój

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/pytest
DATA_DIR=./data .venv/bin/uvicorn --factory app.main:app_from_env --reload --port 8000
```
