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
- Linia dla rozgłoszeń i zapisów grupowych opisuje najczęstszy układ ramki. Rzadsze, krótsze warianty
  kończą się w logu ebusd błędem `invalid position` (wartość nie jest wtedy aktualizowana).
- Sporadycznie urządzenie odpowiada z maską `00` (dane nieważne); ebusd nie umie tego odfiltrować,
  więc pojedynczy odczyt może być błędny (w 18-godzinnym logu: 1 na ok. 12 tys. ramek).
- Wiele ramek pozostaje nierozpoznanych: to rejestry o nieopisanym znaczeniu.
- Zweryfikowane przez wstrzyknięcie ramek z logu do ebusd 26.1.26.1, nie na żywej magistrali.

## Regeneracja `ariston_nimbus50s.csv` z nowego logu

Potrzebny jest surowy log ebusd. W dodatku HA trzeba dopisać do `commandline_options`:

```
--lograwdata --lograwdatafile=/config/ebusd_raw.log --lograwdatasize=102400
```

```sh
python3 tools/gen.py ariston.csv ebusd_raw.log ariston_nimbus50s.csv tools/extra_registers.csv
```

Generator dopasowuje ramki do definicji tak jak ebusd (ZZ, PBSB, początek danych) i dodaje linie
dla układów, które nie są rozpoznawane albo są rozpoznawane tylko częściowo. Rejestr dostaje nową
linię tylko wtedy, gdy dany układ występuje wyraźnie częściej niż dotychczasowe źródła tej wartości.

Weryfikacja w prawdziwym ebusd (wymaga Dockera):

```sh
mkdir -p /tmp/ebusd_cfg && cp ariston.csv ariston_nimbus50s.csv _templates.csv /tmp/ebusd_cfg/
python3 tools/frames.py ebusd_raw.log > /tmp/frames.txt
tools/runall.sh /tmp/ebusd_cfg /tmp/frames.txt | grep -E 'unknown|error' | less
```

`tools/extra_registers.csv` zawiera rejestry, których nie ma w `ariston.csv`, wraz ze źródłem
(dokumentacja [ysard/ebusd_configuration_chaffoteaux_bridgenet](https://github.com/ysard/ebusd_configuration_chaffoteaux_bridgenet)
korelacja z logiem i integracją Ariston w HA albo menu serwisowe sterownika). Nowo rozpoznane rejestry
wystarczy tam dopisać i wygenerować CSV ponownie. Rejestr z podanym adresem urządzenia dostaje linię `r`,
czyli ebusd odpytuje go aktywnie.

Rejestry z menu serwisowego rozpoznaje się tak: przy włączonym surowym logu przejść menu sterownika,
fotografując ekrany. Panel (adres `70`) pyta wtedy o każdy wyświetlany parametr komendą `2001`,
a odpowiedź niesie wartość oraz zakres min/max, które wystarczy zestawić z ekranem.

## Licencja

GPL-3.0, jak repozytorium źródłowe.
