# Obnova vymazaných dát — návod

Obnovuje zmazané súbory z disku, USB kľúča alebo pevného disku metódou
**file carving** (hľadá súbory podľa ich signatúr). Funguje na **Windows aj
Linux**, zdroj **iba číta** — nikdy naň nezapisuje.

Máš tri možnosti ovládania:
- **Obnova dat PRO.pyw** (odporúčané) — veľké okno s dátumom, výberom typov,
  náhľadom v aplikácii a výberom, čo preniesť späť. Viď hneď nižšie.
- **Obnova dat.pyw** — jednoduché okno (len vyber a spusti).
- **obnova_dat.py** — príkazový riadok pre pokročilých (úplne dole).

---

## PRO aplikácia (odporúčaná) — `Obnova dat PRO.pyw`

Dvojklik → potvrď **UAC (Áno)**. V okne:

1. **Odkiaľ obnoviť** — vyber disk/USB zo zoznamu (tlačidlo *🔄 Hľadať disky/USB*)
   alebo image súbor.
2. **Čo hľadať** — zaškrtni kategórie: Fotky, Videá, Dokumenty, Archívy, Hudba,
   Spustiteľné (EXE), Databázy.
3. **Dátum (voliteľné)** — zadaj *Od* a *Do* (napr. `2021-01-01`). Filtruje podľa
   dátumu **zabudovaného v súbore** (EXIF fotky, PDF, Word/ZIP, MP4). Typy bez
   vnútorného dátumu zahrnieš zaškrtnutím *„zahrnúť aj bez dátumu"*.
4. Klikni **🔍 Obnoviť / skenovať**. Nájdené súbory sa objavia v tabuľke.
5. **Náhľad (sandbox)** — klikni na riadok a vpravo uvidíš súbor: obrázky ako
   náhľad, ostatné ako údaje + prvé bajty. **`.exe` sa NIKDY nespúšťa** — len sa
   zobrazia jeho údaje. Súbory sú zatiaľ v dočasnej „karanténe", nie v počítači.
6. **Zaškrtni** (stĺpec ✓) tie, ktoré chceš, a klikni
   **💾 Preniesť vybrané do počítača** → vyber priečinok. Čo nezaškrtneš, zahodí
   sa (ostane nezáchránené).

> Náhľad JPG v okne je krajší s knižnicou Pillow: `pip install pillow`
> (bez nej sa pri JPG zobrazia aspoň rozmery a údaje; PNG/GIF idú aj tak).

Spúšťať sa dá aj cez **`Spustit PRO (Windows).bat`**.

---

## Jednoduchá aplikácia — `Obnova dat.pyw`

**Windows:**
1. Dvojklik na **`Obnova dat.pyw`** (alebo na `Spustit obnovu (Windows).bat`,
   ak by dvojklik na .pyw nefungoval).
2. Vyskočí okno **UAC** — potvrď „Áno" (treba práva na čítanie disku).
3. V okne: vyber **disk/USB** (alebo image súbor) → vyber **priečinok na iný
   disk** → klikni **▶ Spustiť obnovu**.
4. Keď dobehne, klikni **📂 Otvoriť priečinok**.

**Linux:** v termináli spusti s právami:
```
sudo python3 "Obnova dat.pyw"
```

> Tip: ak chceš z `.pyw` spraviť skutočné `.exe` (jeden súbor bez Pythonu),
> nainštaluj `pip install pyinstaller` a spusti:
> `pyinstaller --onefile --noconsole --name "Obnova dat" "Obnova dat.pyw"`

---

## Príkazový riadok (pre pokročilých) — `obnova_dat.py`

## Čo potrebuješ
- Nainštalovaný **Python 3.8+** (over cez `python --version`).
- Žiadne ďalšie knižnice — používa iba štandardnú výbavu Pythonu.
- **Administrátorské / root práva** na čítanie surového disku.

## ⚠️ Dve dôležité pravidlá
1. Keď zistíš, že si niečo zmazal, **prestaň daný disk/USB používať** a nič naň
   neukladaj. Každý nový zápis môže prepísať to, čo chceš zachrániť.
2. **Výstupný priečinok (`-o`) daj na INÝ disk** než je zdroj (napr. zdroj je
   USB `E:`, výstup daj na `D:\obnovene`).

## Postup

### 1. Nájdi svoj disk
```
python obnova_dat.py --list
```

### 2. Spusti obnovu

**Windows** (spusti terminál / PowerShell ako *Administrátor*):
```
python obnova_dat.py \\.\PhysicalDrive1 -o D:\obnovene
```
alebo konkrétny oddiel (písmeno jednotky):
```
python obnova_dat.py \\.\E: -o D:\obnovene
```

**Linux** (cez `sudo`):
```
sudo python3 obnova_dat.py /dev/sdb -o ~/obnovene
```

**Z image súboru** (napr. zálohy cez `dd`):
```
python obnova_dat.py disk.img -o obnovene
```

### 3. Výsledok
Obnovené súbory nájdeš v zadanom priečinku ako
`obnovene_000001.jpg`, `obnovene_000002.png`, …

## Prepínače
| Prepínač | Význam |
|----------|--------|
| `--list` | vypíše dostupné disky |
| `-o PRIECINOK` | kam uložiť výsledky (default `obnovene`) |
| `--all` | zapne aj ďalšie typy: mp4, mp3, zip archívy, sqlite, doc, wav… |
| `--only jpg,png,pdf` | obnoví len vybrané prípony (rýchlejšie) |
| `-q` | bez priebežných výpisov |

## Čo program dokáže obnoviť
Základ: **JPEG, PNG, GIF, PDF, ZIP** (vrátane DOCX/XLSX/PPTX).
S `--all` navyše: MP4, MP3, GZIP, RAR, 7z, SQLite, staré DOC, WAV.

## Obmedzenia (dôležité vedieť)
- **Nevie obnoviť pôvodné názvy** súborov ani priečinky — súbory sa len
  očíslujú. Je to tak u každého carvingu, lebo názvy sú uložené inde
  v súborovom systéme, nie v samotnom súbore.
- Niektoré nálezy môžu byť **neúplné** (ak už boli čiastočne prepísané) alebo
  **falošné** (náhodné dáta, ktoré vyzerajú ako začiatok súboru). Falošné
  jednoducho zmaž.
- Čím väčší disk, tým dlhšie to trvá (číta sa celý).
- Veľmi fragmentované súbory (rozhádzané po disku) sa nemusia poskladať celé.

## Overené
Program bol otestovaný na umelom „disku" so zabudovanými súbormi — JPEG, PNG
a ZIP sa obnovili **bajt za bajtom identicky**, PDF plne otvoriteľný.
