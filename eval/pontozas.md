# Pontozás

Egy eset = egy jelentés, amit a folyamat a befagyasztott bemenetből írt. A
jelentést a `reference.yaml` válaszkulcsához mérjük. A szempontok a
`.claude/skills/befektetesi-jelentes/SKILL.md` szabályaiból jönnek: ha a SKILL
változik, ezt a fájlt is nézd át.

Minden döntés **igen/nem**. Nincs 1-10-es skála: azon a bíró és te is
máshogy osztályoznátok, és két futás közti különbség zajjá válna.

Utolsó átdolgozás: 2026-11-13, a `fable_5.2_pontozasi_javaslat.md` alapján
(1-6. pont és a kisebb javítások), a D1-D5 döntésekkel együtt
(`elteresek.md`).

## Ki pontoz

- **Kód:** ami szó szerinti egyezéssel vagy számolással eldönthető: K3, a
  `jeloles_elol`, a `tilos_szamok`, a D pont és a számok egyeztetése a B
  pontban.
- **LLM-bíró:** minden, amihez ítélet kell. A bíró **helyben fut** (`claude -p`),
  és megkapja a nyersanyagot (a digest kimenetét), **a képeket is**, a jelentést
  és a `reference.yaml`-t. A Langfuse csak megjeleníti az eredményt, nem ott fut
  a bíró.
- **A bíró minden kizáró ítéletnél szó szerint idézi a kifogásolt mondatot.**
  Idézet nélküli kizárás érvénytelen. Így egy téves ítélet másodpercek alatt
  ellenőrizhető.

## Melyik rendszerre érvényes

A szabályzat a Python-rendszer kimenetére készült. Az n8n-es workflow sima
szöveget ír, és több dolgot nem kap meg, ezért nem minden ellenőrzés értelmes
rá. A pontozó a futtatott rendszert (`python` | `n8n`) bemenetként kapja.

| Ellenőrzés | python | n8n |
|---|---|---|
| D3 képek (hivatkozás, fájl létezik) | igen | kimarad: az n8n nem csatol képet |
| D4 Összkép | `## Összkép` fejléc | `🧭 Összkép` sor |
| D7 nincs Markdown-jelölés (`**`, `#`, táblázat, képhivatkozás) | kimarad | igen |
| „Grafikonok nélkül” jelölés (K3) | igen | kimarad, amíg a prompt nem írja elő |
| „Mi változott” (előző jelentés) | igen | kimarad: nem kap előző jelentést |

A manifestben esetenként jelölve van, melyik rendszeren futtatható
(`rendszer: [python, n8n]`). Az n8n-nek nincs web-forrása: a p10 csak
Pythonon fut, a p09 n8n-en csak a hírlevéllel. Az n8n X-posztonként
legfeljebb 2 képet küld: a bővítésnél figyelni kell, hogy ne vesszen el
grafikon (a p06 harmadik képe szöveges kép, ott nem vész el tény).

## 1. Kizáró hibák

Ha bármelyik előfordul, az eset **megbukik**. A szempontok (A-E) ettől még
elkészülnek, lásd a 3. részt.

| Kód | Hiba | Ki dönti el |
|---|---|---|
| K1 | **Kitalált tény:** (a) olyan árszint, dátum vagy százalék, ami se a nyersanyag szövegében, se a képeken, se a referenciában nincs; (b) **megfordított vagy kitalált irány** (pl. „shortol”, ahol a szerző vesz); (c) **kitalált pozíció** (pl. „eladta az ETH-t”, ha ilyet nem mond) | LLM-bíró, a képek megnézésével, idézettel |
| K2 | **Saját tanács vagy minősítés:** „érdemes venni”, „jó ötletnek tűnik” | LLM-bíró |
| K3 | **Hiányzó kötelező jelölés:** a `kotelezo_jeloles` bármelyik szövege nincs meg **abban a szakaszban, amelyik elemre vonatkozik** | kód |
| K3e | **A jelölés nem legelöl áll**, ahol a referencia `jeloles_elol: true` (hibaesetek: nincs felirat; a riport szövege nem jött át) | kód |
| K4 | **Tilos tartalom:** a `tilos_szamok` vagy a `tilos` lista bármelyik eleme megjelenik | kód (`tilos_szamok`), bíró (`tilos`) |
| K5 | **Állítás felirat vagy szöveg nélkül:** `felirat: NINCS` vagy `kinyerés: SIKERTELEN` mellett olyat állít a tartalomról, ami a nyersanyagban nincs meg. A cím, a leírás és a felvezető ismertetése (jelölve, hogy onnan való) nem K5; ha a referencia ezt is tiltja, az K4 | LLM-bíró |

Szabályok a K1-hez és a K4-hez:

- **K1:** a referencia nem sorol fel minden számot a grafikonokról. Ha a
  jelentés olyan számot ír, ami a képen tényleg ott van, az nem kitalált,
  akkor sem, ha a referenciából hiányzik. Ezért kell a bírónak a képeket
  néznie. Ugyanez a pozícióra: amit a szerző kimond, az nem kitalált.
- **K4, `tilos_szamok`:** ide csak olyan szám kerülhet, ami **semmilyen
  mondatban nem jogos** (pl. az előfizetés ára).
- **K4, a felirat hibás száma vagy neve (D2 döntés, 2026-11-13):** a hibás
  érték **semmilyen formában** nem kerülhet a jelentésbe, hibajelzésként sem
  („a feliratban 600 szerepel, a helyes kb. 58 ezer”), mert csak félrevezet.
  Csak a helyes érték. Ezek a `tilos` listában vannak, és a bíró ítéli meg,
  mert az írásmód változhat („Ono”, „ONO”).
- **Amit a bíró számon kér, annak a `tilos` listában kell lennie.** A
  `megjegyzes` csak háttér: ami csak ott áll, azért a bíró nem ad kizáró
  hibát (a próbán a p02 „5890”-ét háromból háromszor átengedte, amíg csak
  a megjegyzésben szerepelt).
- **Egy mondat egy hiba.** A bíró egy hibát egyszer jelöl; ha a `tilos`
  lista leírja, K4-ként. Ha mégis több kódot ad ugyanarra a mondatra (az
  idézetek fedik egymást), a kód egy hibának veszi, és a számlálásban egy
  kód alá teszi, ebben a sorrendben: K4, K5, K1c, K1b, K1a, K2. Ha a
  csoportban van szabályváltozásból eredő tétel, a hiba ahhoz tartozik.

### Szabályváltozásból eredő bukás

Három döntés eltér a mostani SKILL-től, ezért az alapmérésben a modell, ami
a SKILL-t követi, ezeken megbukhat:

- **D2:** a SKILL mindkét érték leírását kéri (a hibásat is);
- **D3:** a SKILL a jelölést a tartalom alá teszi (`jeloles_elol`);
- **D6:** sikertelen kinyerésnél a SKILL és a digest a felvezető
  ismertetését megengedi („csak a cím és a felvezető”), a felhasználó
  szabálya szerint csak a cím ismertethető (p10).

Hogy a SKILL-módosítás hatása látsszon, ezek a `tilos`-elemek `szabaly: D2`
vagy `szabaly: D6` jelölést kapnak, a `jeloles_elol` pedig a D3-hoz
tartozik, és az eredményben **külön oszlopban** szerepelnek:

- „megbukott” = bármelyik kizáró hiba;
- „megbukott, csak új szabály miatt” = kizáró hiba csak az új szabályokból
  (az eredmény megnevezi, melyikből).

## 2. Szempontok

| Szempont | Pont | Hogyan |
|---|---|---|
| **A. Állítások lefedése** | 40 | a referencia `allitasok` közül hány szerepel: a `leiras` lényege megjelenik, és az eszköz vagy az irány nem téves. A `kritikus: true` állítás duplán számít |
| **B. Szintek és időtáv** | 25 | lásd lent |
| **C. Meggyőződés, pozíció** | 10 | a lefedett állításoknál a jelentés nem állítja erősebbnek vagy gyengébbnek, mint az eredeti („figyeli” ≠ „vesz”), és a pozíciót helyesen adja vissza |
| **D. Forma** | 15 | gépi ellenőrzések, lásd lent; mindegyik egyenlő súlyú |
| **E. Olvashatóság** | 10 | LLM-bíró: nincs felfújt fontosság (5), nincs megmagyarázatlan nyers szakszó (5); a meta-nyelvet és a bekezdéshosszt a D méri |

Az A és a C pontot a bíró adja, állításonként igen/nem válasszal. Az E
pontnál a bíró nem igen/nem választ ad, hanem **idézettel felsorolja**, amit
talált (felfújás; nyers szakszó): ha a lista üres, megvan a pont. A kód
ellenőrzi, hogy az idézet benne van-e a jelentésben. (Az igen/nem alak a
próbán három azonos bírálatpárból kettőben átbillent.) A bevett tőzsdei
szavak listája a bíró promptjában van.

**Ha egy szempontra nincs mit mérni, kimarad az átlagból:** a B, ha az
esetben nincs szint és időtáv; a C, ha egy állítás sincs lefedve.

### B. Szintek és időtáv

1. **A számok egyeztetését kód végzi.** Egységesíti az írásmódot („58 ezer”,
   „58k”, „58 000”, „58.000”, tizedesvessző), és megnézi, a referencia
   szintjei közül melyik szerepel a jelentésben.
2. **Egy szinthez több elfogadott érték vagy tartomány tartozhat** a
   referenciában (pl. a chart pontos száma és a kerekített érték). Az
   egyezés: bármelyik elfogadott érték ±1%-on belül, vagy a tartományon
   belül. Formája a `szintek` listában: szám (`58000`), több elfogadott érték
   (`[58000, 58892]`) vagy tartomány (`"58000-60000"`).
3. **A bíró csak azt dönti el**, hogy a megtalált szám a jó eszközhöz és a jó
   szerephez tartozik-e (pl. a 79 000 feltételként, nem célként).
4. Ugyanaz a szint több állításnál is szerepelhet (p02: 79 000); állításonként
   számít.
5. A `szintek` nem csak árat tartalmazhat (napok, százalékok): ugyanígy
   egyeztetjük.

**Képlet, állításonként:**
(egyező szintek + egyező időtávok) / (szintek száma + nem üres időtávok száma).
A B pont ezek átlaga a készletben.

### D. Gépi formaellenőrzések

1. **nincs gondolatjel a folyó szövegben.** Kivétel, ahová a SKILL sablonja
   maga teszi: a `#` fejléc; a grafikonlista sorában a címke utáni elválasztó
   (`- **12:31** — …`); a képaláírás (`![… — …](…)`); a kötelező jelölés sora
   („Felirat nélkül — csak cím és leírás alapján.”); az idézett eredeti cím.
2. nincs Markdown-táblázat
3. legfeljebb 5 kép, mindegyik önálló sorban, és a fájl létezik (csak python)
4. van Összkép szakasz (python: `## Összkép`; n8n: `🧭 Összkép`)
5. videónként az összefoglaló (a grafikonlista nélkül) legfeljebb 200 szó
6. nincs meta-nyelv a szó szerinti listából („az elérhető adatok alapján”,
   „a képkockákból kiderül”, „az átirat elemzése szerint”)
7. nincs Markdown-jelölés (csak n8n)
8. a jelentés minden linkje szerepel a bemenetben
9. ahol a szakasznak van állítása, van „Röviden” sor (csak python)
10. a „Lényeg” bekezdései legfeljebb 3-4 mondatosak

A reklám-tartalmat **nem kód** ellenőrzi: a „kedvezmény” vagy a „Discord” szó
jogosan is előfordulhat. Ezt a bíró nézi az eset `tilos` listája alapján (K4).

Ezek a formaellenőrzések referencia nélkül is futnak, ezért az **éles
jelentéseken is** lefuttathatók: ez a monitoring első rétege. (Példa, amit
elkapnak: 155 éles jelentésben volt táblázat, mind 2026 augusztusából és
szeptemberéből.)

## 3. Az eredmény

**Elsődlegesen szempontonként nézzük**, nem egy összpontszámként:

- az átment / megbukott arány, és ebből a „csak D2/D3 miatt” külön;
- kizáró hibánként, hány esetben fordult elő;
- az A, B, C, D, E pont külön-külön, a készlet átlagában.

**Az A-E pontok kizáró hiba mellett is elkészülnek.** A kizáró hiba csak az
átment/megbukott eredményt dönti el. Különben egy szabályváltozás (D2, D3)
sok esetet pontszám nélkül hagyna, és a SKILL-módosítás előtti és utáni
összevetés csak a szabályt mérné, a tartalom minőségét nem.

Ha összpontszám kell, **két szám** legyen: tartalom (A+B+C, 75 pontból) és
forma (D+E, 25 pontból). A forma szinte ingyen megvan, ezért egyetlen 0-100-as
szám és egy 70-es határ keveset mondana. Az átmenési határ a kalibrálás után
dől el.

**Negatív esetek** (nincs elvárt állítás: felirat nélküli videó, sikertelen
kinyerés, mém): átment, ha nincs kizáró hiba (a `jeloles_elol`-lal együtt). A
D pontot ezeknél is kiszámoljuk, de külön, mint a többi esetnél, és nem dönti
el az átmenést. Pontszámot nem kapnak, és az A-C átlagba nem számítanak bele.

## 4. Zaj és összehasonlítás

Ugyanaz a bemenet kétszer futtatva más jelentést ad, és az igen/nem
kimenetekhez két futás kevés: egy 30%-ban előforduló hiba kb. 49% eséllyel
egyik futásban sem jön elő. Ezért:

- **Az alapállapotot esetenként 4-5-ször futtatjuk.** A futások közti eltérés
  a jelentésíró zaja.
- **A bíró zaját is mérjük:** ugyanazt a jelentést 2-3-szor bíráltatjuk
  (`judge.py --repeat 3`). Az összesítő kiírja, hány jelentésnél azonos az
  átment/megbukott eredmény és a kizáró kódok halmaza, és döntésenként
  hányszor tért el a bíró önmagától. A fő táblák jelentésenként az első
  bírálatot használják, az ismétlések csak a zajt mérik.
- **A jelentéseket és az ítéleteket eltároljuk.** Az ítéletek a
  `runs/<időbélyeg>/biro/<modell>-<prompt-lenyomat>/` mappába kerülnek: ha a
  prompt vagy a modell változik, az újrabírálás új mappába megy, a régi
  megmarad, és a kettő összevethető. Minden ítélet rögzíti a tényleges
  modellazonosítót (nem csak az „opus” aliast) és a referencia lenyomatát.
- Két változat (pl. SKILL-módosítás előtt és után) között csak az a
  különbség számít, ami **nagyobb a zajnál**.
- Mindig az **esetenkénti** változást nézd, ne csak az átlagot: egy javítás
  könnyen elront egy másik esettípust. Kis készletnél egyetlen eset is
  több százalékpontot mozdít az arányon.

## 5. A pontozó próbája

Mielőtt a pontozót élesen használjuk: egy jó jelentésbe egyenként hibát
teszünk, és megnézzük, elkapja-e:

- kitalált szám; saját tanács; törölt kötelező jelölés; a jelölés a szakasz
  végére tolva; megfordított irány; kitalált pozíció; „Ono” az „Ondo” helyett;
  táblázat; gondolatjel a folyó szövegben (és a sablon gondolatjele, amit
  nem szabad hibának vennie).

## 6. Kalibrálás

A próbafutásnál:

- **minden kizáró ítéletet** átnézel (a bíró idézete alapján): jogos volt-e;
- a próbakészlet mind a 10 esetét te is lepontozod, a bírótól függetlenül
  (a bővítés után az első 15-öt).

Ahol eltér:

- ha a **bíró** tévedett: a bíró promptját javítjuk;
- ha a **referencia** volt hiányos: a `reference.yaml`-t javítjuk, és
  `allapot: jovahagyva` marad;
- ha a **szabály** volt rossz: a SKILL.md-t és ezt a fájlt.

A referenciát soha ne igazítsd a jelentéshez csak azért, hogy átmenjen.

## A reference.yaml mezői, és ki használja őket

| Mező | Ki használja |
|---|---|
| `allitasok` (`leiras`, `eszkoz`, `irany`, `kritikus`) | bíró: A, C; K1 (irány) |
| `szintek`, `idotav` | kód és bíró: B |
| `pozicio`, `meggyozodes` | bíró: C; K1 (pozíció) |
| `invalidacio` | bíró: B (a szint szerepe) |
| `kotelezo_jeloles` | kód: K3 |
| `jeloles_elol` | kód: K3e (kizáró, `szabaly: D3`) |
| `tilos_szamok` | kód: K4 |
| `tilos` (elemenként opcionális `szabaly: D2` vagy `D6`) | bíró: K4 |
| `megengedett` | bíró: ami kifejezetten szabad (nem K1, nem K5) |
| `grafikonok` | bíró: segítség a képek olvasásához; nem elvárás |
| `megjegyzes` | háttér a bírónak és az embernek; kizáró hiba nem alapulhat rajta |
| `referencia_forrasa` | csak ember |
