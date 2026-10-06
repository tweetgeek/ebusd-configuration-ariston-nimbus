# ebusd – Ariston Nimbus 50S

Konfiguracja [ebusd](https://github.com/john30/ebusd) dla pompy ciepła Ariston Nimbus 50S (bez kotła),
oparta na [wrongisthenewright/ebusd-configuration-ariston-bridgenet](https://github.com/wrongisthenewright/ebusd-configuration-ariston-bridgenet)
(commit `f2c9a63`), która powstała dla hybrydy Genus One + Nimbus 70M.

## Pliki

| Plik | Opis |
|---|---|
| `ariston.csv` | Plik z repozytorium źródłowego z zakomentowanymi liniami `r`/`w` obwodu `boiler` (adres `3c`). Kotła nie ma na magistrali, więc każde zapytanie kończyło się timeoutem. |
| `ariston_nimbus50s.csv` | Dodatkowe linie pasywne: rejestry znane z `ariston.csv`, ale w układach ramek, które wysyła Nimbus 50S. |
| `_templates.csv` | Bez zmian względem repozytorium źródłowego. |

## Instalacja (dodatek eBUSd w Home Assistant)

1. Skopiuj trzy pliki CSV do `/addon_configs/<slug>/ebusd_config/`.
2. Ustaw `--configpath=/config/ebusd_config/` oraz `--scanconfig=none`
   (urządzenia Ariston nie odpowiadają na standardową identyfikację `0704`).
3. Zrestartuj dodatek.

## Ograniczenia

- Nazwy z sufiksem (`z1_night_temp_200e`, `hybrid_LWT_setpoint_200f`) istnieją dlatego, że ebusd
  odrzuca dwie pasywne linie o tej samej nazwie i nie ładuje wtedy całej konfiguracji.
- Linie rozgłoszeń grupowych dekodują tylko wspólny początek ramek zaobserwowanych w logu.
- Wiele ramek pozostaje nierozpoznanych: to rejestry o nieopisanym znaczeniu
  (m.in. zapisy `131e2020`, większość rozgłoszeń `13fe2010`).
- Zweryfikowane przez wstrzyknięcie ramek z logu do ebusd 26.1.26.1, nie na żywej magistrali.

## Regeneracja `ariston_nimbus50s.csv` z nowego logu

Wymaga Dockera i logu ebusd z ramkami `received unknown ... cmd`.

```sh
mkdir base && cp ariston.csv _templates.csv base/        # konfiguracja bez uzupełnienia
python3 tools/frames.py ebusd.log > frames.txt           # unikalne ramki z logu
tools/runall.sh "$PWD/base" "$PWD/frames.txt" > base.out # co ebusd rozpoznaje bez uzupełnienia
python3 tools/gen.py ariston.csv base.out ebusd.log ariston_nimbus50s.csv
tools/runall.sh "$PWD" "$PWD/frames.txt" | grep -c 'received unknown'   # weryfikacja
```

## Licencja

GPL-3.0, jak repozytorium źródłowe.
