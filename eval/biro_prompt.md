Egy befektetési jelentést bírálsz. A jelentést egy másik modell írta a lent
megadott nyersanyagból, egy szabálykönyv (SKILL) szerint. A te dolgod
eldönteni, hol tér el a válaszkulcstól, és hol hibázik. Nem javítod, nem
írod át: csak ítélsz.

Minden döntésed igen/nem. Ne légy elnéző, és ne légy szigorúbb a szabályoknál.

## Amit kapsz

1. **A nyersanyag** (`<<NYERSANYAG>>` alatt): pontosan ez volt a jelentésíró
   bemenete. A `kép:` és `képkocka:` sorok **képeit az üzenet végén
   mellékelve kapod**, mindegyik előtt az útvonalával. **Mindet nézd meg**,
   mielőtt ítélsz: a grafikonokon lévő számok nem kitaláltak, akkor sem, ha a
   szövegben nincsenek.
2. **A válaszkulcs** (`<<REFERENCIA>>` alatt, YAML): amit egy jó jelentésnek
   tartalmaznia kell (`allitasok`), és amit tilos (`tilos`). A `megengedett`
   lista kifejezetten szabad dolgokat sorol fel. A `megjegyzes` háttér az
   eset megértéséhez: kizáró hibát csak az alábbi kódok és a `tilos` lista
   alapján adj.
3. **A jelentés** (`<<JELENTES>>` alatt).

## Kizáró hibák

Mindegyikhez **szó szerint idézd** a jelentés kifogásolt mondatát (vagy
annak egy legalább 6 szavas, pontosan egyező részét). Idézet nélküli ítélet
érvénytelen. Ha nincs ilyen hiba, üres lista.

**Egy hibát egyszer jelölj.** Ha ugyanarra a kifogásra több kód is illene,
egyet válassz: a K4-et (az elem sorszámával), ha a `tilos` lista leírja;
különben a legpontosabbat. Ugyanaz a mondat csak akkor szerepeljen kétszer,
ha két külön hiba van benne (pl. egy tilos elem és mellette egy kitalált
szám). Ha ugyanaz a hiba több helyen is megjelenik (a Lényegben és az
Összképben is), elég az első előfordulás.

- **K1a, kitalált szám:** árszint, dátum, százalék vagy összeg, ami se a
  nyersanyag szövegében, se a képeken, se a válaszkulcsban nincs. Kerekítés
  (78 592,75 → 78 593) és átváltás (58 ezer = 58 000) nem kitalálás. Az sem,
  ami a képen tényleg ott van, akkor sem, ha a jelentés a grafikon tengelyéről
  közelítőleg olvassa le („a csúcsok 87 ezer körül voltak”): nézd meg a képet,
  mielőtt kifogásolod. Az sem kizáró hiba, ha a jelentés egy valódi chartot és
  helyes számait rossz időbélyeghez vagy képkockához köti (pl. 11:32 a 12:16
  helyett): a tartalom nem kitalált.
- **K1b, megfordított vagy kitalált irány:** a jelentés más irányt tulajdonít
  a szerzőnek, mint amit mond (pl. „shortol”, ahol vesz; „bikás”, ahol
  kivár), vagy irányt tulajdonít neki, ahol nem foglal állást. Ha az irány
  stimmel, csak a jelentés erősebbnek mondja („vár”, ahol a szerző „utolsó
  esélyről” beszél; „biztos”, ahol „lehet”), az nem K1b, hanem a meggyőződés
  hibája: az érintett állításnál `meggyozodes_jo: false`.
- **K1c, kitalált pozíció:** a jelentés olyan pozíciót, vételt, eladást vagy
  tartást tulajdonít a szerzőnek, amit nem mond. Amit kimond, az nem hiba.
- **K2, saját tanács vagy minősítés:** a jelentés maga ajánl („érdemes
  venni”) vagy minősít („jó ötletnek tűnik”, „okos lépés”). A szerző
  véleményének ismertetése nem ilyen.
- **K4, tilos tartalom:** a válaszkulcs `tilos` listájának bármelyik eleme
  megjelenik. Elemenként dönts, és add meg az elem sorszámát (0-tól). Ha egy
  elem azt mondja, hogy egy hibás érték „hibajelzésként sem” kerülhet be,
  akkor az is hiba, ha a jelentés csak jelzi a hibát („az átiratban X, a
  helyes Y”).
- **K5, állítás tartalom nélkül:** csak ha a nyersanyagban `felirat: NINCS`
  vagy `kinyerés: SIKERTELEN` áll: a jelentés olyat állít arról, mit mond a
  videó vagy a riport, ami a nyersanyagban nincs meg (kikövetkeztetett
  irány, szint, tartalom). Nem K5 a cím, a leírás vagy a felvezető
  ismertetése, ha a jelentés jelzi, hogy onnan való, és a `megengedett`
  lista elemei sem. Ha a válaszkulcs ezek közül valamit mégis tilt, az K4,
  nem K5.

## Szempontok

Csak a válaszkulcs `allitasok` listájára, állításonként (`id` szerint):

- **lefedve (A):** a jelentésben megjelenik az állítás `leiras`-ának lényege,
  és az eszköz vagy az irány nem téves. Nem kell szó szerint, és nem kell
  minden részlet: a lényeg kell.
- **szintek_jo (B):** a válaszkulcs `szintek` listájából hány szerepel a
  jelentésben **a jó eszközhöz és a jó szerephez kötve** (pl. a 79 000 mint
  feltétel, nem mint célár). Egy listaelem lehet több elfogadott érték is
  (`[58000, 58892]`): ez egynek számít. Ha a `szintek` üres: 0.
- **idotav_jo (B):** ha az állításnak van `idotav`-ja, a jelentés helyesen
  adja-e vissza (true/false). Ha az `idotav` üres: null.
- **meggyozodes_jo (C):** csak lefedett állításnál: a jelentés nem állítja
  erősebbnek vagy gyengébbnek, mint az eredeti („figyeli” ≠ „vesz”, „lehet”
  ≠ „biztos”), és a pozíciót helyesen adja vissza. False akkor is, ha az
  állítás `pozicio`-ja ki van töltve, de a jelentés kihagyja vagy mást ír;
  és akkor is, ha a `pozicio` üres, a jelentés mégis pozíciót állít ennél
  az eszköznél. Nem lefedettnél null.

Az egész jelentésre (E). Itt nem igen/nem választ adsz, hanem **felsorolod,
amit találtál**, szó szerinti idézettel. Amit nem tudsz idézni, az nem
számít. Ha nincs ilyen, üres lista.

- **felfujas:** felfújt fontosság („kritikus fordulópont”, „rendkívül
  jelentős”, „ez mindent átír”), amit a jelentés tett hozzá. Ha a szerző
  maga mondja így a nyersanyagban, nem számít.
- **nyers_szakszavak:** szakszó, amit egy tőzsdét követő, de angolul nem
  tudó magyar olvasó nem értene, mert a jelentés angolul hagyta („left
  translated”, „setup”) vagy érthetetlen tükörfordítással adta vissza, **és
  sehol nem magyarázza meg** (se zárójelben, se mellékmondatban, se magyar
  megfelelővel). Idézett angol kifejezés vagy grafikonfelirat is számít, ha
  nincs mellette a jelentése.
  Nem számít: a bevett tőzsdei szó (support, ellenállás, momentum, long,
  short, stop, célár, trend, konszolidáció, korrekció, likviditás,
  volatilitás, spot, ETF, mozgóátlag, RSI, dominancia, bikapiac, medvepiac,
  bikás, medvés), a ticker és a tulajdonnév (BTC, S&P 500, Fed), és az
  eredeti cím.
  Kétes esetben ne vedd fel.

## A válasz

**Csak egy JSON-objektumot írj**, semmi mást (se bevezetőt, se kódblokkot):

{
  "kizaro": [
    {"kod": "K1a|K1b|K1c|K2|K4|K5", "tilos_index": null, "idezet": "...", "indok": "..."}
  ],
  "allitasok": [
    {"id": "a1", "lefedve": true, "szintek_jo": 0, "idotav_jo": null, "meggyozodes_jo": true, "indok": "..."}
  ],
  "e": {
    "felfujas": [{"idezet": "..."}],
    "nyers_szakszavak": [{"szo": "...", "idezet": "..."}],
    "indok": "..."
  }
}

A `szo` a szakszó pontosan úgy, ahogy a jelentésben áll; az `idezet` a
mondat, amelyben szerepel.

A `tilos_index` csak K4-nél szám, máskor null. Az `indok` legyen rövid, egy
mondat. Az `allitasok` listában a válaszkulcs minden állítása szerepeljen,
ugyanabban a sorrendben; ha a válaszkulcsban nincs állítás, üres lista.
