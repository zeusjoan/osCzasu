# Oś Czasu — przeglądarka historii lokalizacji Google

Data: 2026-10-29 · Status: do akceptacji

## 1. Cel i powód

Prywatna aplikacja webowa do przeglądania historii lokalizacji eksportowanej z Google Timeline (plik JSON z telefonu). Użytkownik (jedna osoba) wczytuje pliki kilku osób (np. swój i rodziny), przypisuje każdy do osoby i ogląda oś czasu wybranej osoby na mapie: trasy z rodzajem transportu oraz odwiedzone miejsca z nazwą (np. „Lidl, supermarket”) i czasem pobytu.

Typowe pytania, na które aplikacja ma odpowiadać:
- „Gdzie byłem w danym okresie?”
- „Czy byłem w konkretnym sklepie, kiedy i ile czasu tam spędziłem?”

## 2. Cele, nie-cele, anty-cele

**Cele (MVP):**
1. Uruchomienie jako jeden kontener Docker (`docker compose up`) na domowym serwerze; dane trwałe w jednym wolumenie.
2. Zarządzanie osobami i wczytywanie wielu plików na osobę, ze scalaniem bez duplikatów.
3. Ekran dnia: lista wizyt i przejazdów + mapa z trasami (kolor wg transportu) i pinezkami miejsc.
4. Rodzaj transportu z pliku Google, z ręczną korektą.
5. Nazwy i kategorie miejsc z OpenStreetMap, pobierane w tle, z cache i paskiem postępu.
6. Wyszukiwanie po nazwie/kategorii miejsca i zakresie dat; wyniki pogrupowane wg miejsca.

**Nie-cele (może później):** stary format Google Takeout (`Records.json` / `Semantic Location History`), statystyki i mapa cieplna, ręczne nazywanie miejsc, własne zgadywanie transportu, logowanie.

**Anty-cele (porażka, nawet jeśli działa):**
- porównywanie osób między sobą — nie jest celem i nie powinno komplikować UI,
- wysyłanie danych lokalizacyjnych gdziekolwiek poza zapytania o nazwy miejsc do OSM (i kafelki mapy),
- płatne usługi / klucze API (świadomie odrzucono Google Places),
- rozbudowana infrastruktura (osobny serwer bazy, kolejki, build frontendu).

## 3. Ograniczenia

- Jeden użytkownik, dostęp tylko z sieci domowej → **bez logowania**.
- Dane prywatne: nie trafiają do repozytorium (`Timeline.json`, katalog danych w `.gitignore`).
- Publiczne serwery OSM (Nominatim/Overpass): max **1 zapytanie/s**, wymagany sensowny `User-Agent` z kontaktem. Wyniki trwale cache'owane — każde miejsce pytane raz.
- Interfejs po polsku.

## 4. Format danych wejściowych (zweryfikowany na `Timeline.json`)

Nowy format eksportu z urządzenia: **tablica JSON** obiektów, każdy ma `startTime`, `endTime` (ISO 8601, z różnymi strefami — `+01:00`, `Z`) i dokładnie jeden z kluczy:

| Klucz | Liczba w próbce | Zawartość |
|---|---|---|
| `visit` | 1760 | `topCandidate.placeID` (Google), `placeLocation` `"geo:lat,lng"`, `semanticType` (`Home`, `Work`, `Searched Address`, `Unknown`), `probability` |
| `activity` | 1831 | `start`/`end` `"geo:lat,lng"`, `distanceMeters`, `topCandidate.type` (`in passenger vehicle`, `cycling`, `walking`, `skiing`, `in bus`, `in train`, `running`, `sailing`, `in ferry`, `in tram`…) |
| `timelinePath` | 2334 | okna (zwykle 2 h, UTC) z listą `{point:"geo:lat,lng", durationMinutesOffsetFromStartTime}` |
| `timelineMemory` | 1 | podsumowanie podróży — ignorujemy |

Liczby są stringami. Próbka: marzec 2025 – październik 2026, 733 unikalne `placeID`.

**Wizyty zagnieżdżone** *(decyzja Claude)*: `visit.hierarchyLevel` = `"0"` (1673) lub `"1"` (87). Poziom 1 to wizyta wewnątrz innej (np. sklep w galerii). Na liście dnia pokazywana jako podpozycja wcięta pod wizytą-rodzicem (poziom 0 pokrywający ją w czasie). W wyszukiwaniu znajdowalne oba poziomy; w sumach czasu dla „gdzie byłem w okresie” czas liczony raz (poziom 1 nie dodaje się do rodzica, jest osobnym miejscem w wynikach).

**Geometria trasy:** `activity` ma tylko punkt startu i końca. Linię przejazdu buduje się z punktów `timelinePath`, których czas (`startTime` okna + offset) mieści się w `[activity.startTime, activity.endTime]`, uzupełnionych o start i koniec. Brak punktów → linia prosta start–koniec.

Nieznane klucze i nieparsowalne wpisy: pomijać i liczyć (raport po imporcie), nie przerywać importu. Plik w innym formacie (np. obiekt z `semanticSegments` lub stary Takeout) → czytelny komunikat „nieobsługiwany format”.

## 5. Decyzje techniczne

*Wybór technologii był pozostawiony mnie (Claude) — to moja decyzja, nie uzgodniona szczegółowo.*

- **Backend:** Python 3.12 + FastAPI (+ Uvicorn). Import, API, zadanie w tle do pobierania nazw miejsc.
- **Baza:** SQLite (jeden plik w wolumenie danych).
- **Frontend:** statyczny HTML + vanilla JS + **Leaflet** z kafelkami OpenStreetMap, serwowany przez FastAPI. Bez frameworka i kroku budowania.
- **Nazwy miejsc:** OpenStreetMap — najpierw szukanie POI w promieniu ~50 m od punktu wizyty (Overpass), a gdy brak — adres z odwrotnego geokodowania (Nominatim). Wynik: nazwa, kategoria (np. `shop=supermarket` → „supermarket”), adres. Szczegóły zapytań — do decyzji budującego.
- **Docker:** jeden obraz, `docker-compose.yml` z wolumenem `./data` (baza + wgrane pliki) i jednym portem.
- Klucz cache nazw: `placeID` (gdy jest), inaczej zaokrąglone współrzędne. Cache wspólny dla wszystkich osób.

## 6. Działanie aplikacji

### 6.1 Osoby i import
- Tworzenie osoby: wpisanie imienia. Zmiana nazwy i usunięcie osoby (z jej danymi) — tak.
- „Wczytaj plik”: wybór osoby + upload JSON. Plik zapisywany w wolumenie, wpisy parsowane do bazy.
- Wiele plików na osobę; **scalanie — „nowszy plik wygrywa”** *(decyzja Claude)*: Google przelicza historię, więc ten sam dzień w dwóch eksportach bywa pocięty inaczej (przesunięte minuty, inne `placeID`) i dokładne porównanie wpisów nie wystarcza. Dlatego plik obejmuje zakres czasu `[min startTime, max endTime]`; przy imporcie wpisy tej osoby z plików **starszych** (wg daty wgrania) leżące w całości w zakresie nowego pliku są zastępowane jego wpisami. Wpisy spoza zakresu zostają. Ponowne wgranie identycznego pliku nic nie zmienia.
- Usuwanie pojedynczego pliku — **poza MVP** *(decyzja Claude)*; poprawkę robi się wgraniem nowszego eksportu. Każdy wpis pamięta tylko plik, z którego pochodzi (jeden do wielu, bez liczenia referencji).
- Po imporcie: podsumowanie (ile wizyt/przejazdów dodano, ile zastąpiono, ile pominięto jako błędne).

### 6.2 Pobieranie nazw miejsc (w tle)
- Po imporcie nowe miejsca trafiają do kolejki; worker przetwarza ~1/s (dla próbki ~12–15 min).
- Pasek postępu w UI („Nazwy miejsc: 312/733”). Przetrwa restart kontenera (stan w bazie).
- Do czasu pobrania: „Nieznane miejsce” + współrzędne. `Home`/`Work` z pliku wyświetlane jako „Dom”/„Praca” (bez zapytania lub z adresem jako dodatkiem).
- Błąd/timeout → ponowienie później; brak wyniku → sam adres.
- Dla każdego miejsca zapisywany jest **wybrany POI** (wyświetlana nazwa) oraz **lista wszystkich kandydatów POI** z promienia wyszukiwania (nazwa, kategoria, odległość) — potrzebna do wyszukiwania sąsiednich lokali (p. 6.5). Jedno zapytanie Overpass zwraca wszystkich, więc nie zwiększa to liczby zapytań.

### 6.3 Ekran dnia (układ A, zaakceptowany)
- Górny pasek: wybór osoby, wyszukiwanie, „Wczytaj plik”.
- Lewy panel: data z ◀ ▶ i kalendarzem; chronologiczna lista dnia — wizyty (ikona kategorii, nazwa, kategoria, godziny, czas pobytu) na przemian z przejazdami (ikona transportu, km, czas).
- Prawa część: mapa z trasami dnia (kolor wg transportu, legenda) i pinezkami wizyt. Klik w pozycję listy → przybliżenie i podświetlenie na mapie (i odwrotnie: klik w pinezkę podświetla pozycję listy).
- Dzień liczony w **lokalnej strefie czasowej wpisu**; wizyta przechodząca przez północ widoczna w obu dniach.
- Domyślnie otwiera się ostatni dzień z danymi wybranej osoby.

### 6.4 Korekta transportu
- Klik w przejazd → wybór z listy typów. Korekta zapisana w bazie i ma pierwszeństwo przed etykietą Google.
- Korekta jest przypisana do osoby i przedziału czasu, nie do konkretnego wiersza *(decyzja Claude)*: po zastąpieniu wpisów nowszym plikiem stosuje się ją do przejazdu, który pokrywa się z tym przedziałem w ≥ 50 %. Brak takiego przejazdu → korekta przepada (bez komunikatu).

### 6.5 Wyszukiwanie
- Pola: tekst (nazwa lub kategoria, bez rozróżniania wielkości liter i polskich znaków), zakres dat od–do, osoba (domyślnie bieżąca; opcja „wszystkie”).
- Wyniki pogrupowane wg miejsca: nazwa + adres, liczba wizyt, łączny czas; rozwijana tabela wizyt (data, od–do, czas, link „pokaż dzień”).
- **Sąsiednie lokale (decyzja użytkownika: pewność ważniejsza niż czystość wyników):** wyszukiwanie przeszukuje nie tylko wybrany POI, ale wszystkich kandydatów zapisanych dla miejsca (p. 6.2). Wizyty znalezione tylko przez kandydata, nie przez wybraną nazwę, są pokazywane w tej samej grupie lokalu, ale wyraźnie oznaczone „możliwie tutaj” (np. inny styl i dopisek, jaki lokal był wybrany jako główny), i liczone osobno od pewnych w sumach (np. „23 wizyty · +4 możliwe”).
- Pusty tekst + zakres dat = „gdzie byłem w tym okresie” — wszystkie miejsca z tego okresu, sortowane malejąco wg łącznego czasu.

## 7. Pozostawione budującemu
- Struktura kodu, schemat bazy, kształt API.
- Dokładne zapytania Overpass/Nominatim, promień, wybór „najlepszego” POI.
- Kolory i ikony transportu/kategorii, mapowanie tagów OSM na polskie nazwy kategorii.
- Testy (zalecane: parser na podstawie fragmentów `Timeline.json` z zanonimizowanymi współrzędnymi, logika scalania, wyszukiwanie).
- Dostrojenie wyglądu poza układem z p. 6.3.
