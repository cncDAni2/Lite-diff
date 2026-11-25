# LiteDiff – részletes leírás

Könnyű, VS Code-szerű git diffnéző Windowsra, több repóhoz egyszerre. Python + pywebview (WebView2) + Monaco Editor (a VS Code szerkesztőkomponense). Kb. 220 MB privát memória.

## Indítás

- `LiteDiff.exe` (lásd lent: EXE készítése), vagy forrásból: `<Python 3.12>\pythonw.exe LiteDiff.pyw`
- Hibakereséshez (fejlesztői eszközökkel): `.venv\Scripts\python.exe LiteDiff.pyw --debug`
- A `LiteDiff.pyw` az alap Python 3.12 `pythonw.exe`-jével fut, és a `.venv\Lib\site-packages` csomagjait tölti be, így nem indul külön venv-indító folyamat.

## Funkciók

### Változások (bal felső blokk)
- Repók listája: a ＋ gombbal mappa adható hozzá; ha a mappa nem repó, az almappáiban lévő repók kerülnek be. Sorrend: a repó sorát húzva. Eltávolítás: ✕ (a mappához nem nyúl).
- Minden repó alatt a commitolatlan változások mappafában, alapból kibontva, az egygyermekes mappák összevonva. Jelölések: ➕ új, ✏️ módosított, 🗑️ törölt, ➡️ átnevezett, ⚠️ ütközés. A staged és unstaged változások együtt látszanak, a `HEAD`-hez képest.
- A repó sorára kattintás összecsuk/kinyit, és a Commitok blokk erre a repóra vált.
- **Ágváltó:** a `⎇ ág` feliratra kattintva lista a helyi és távoli ágakkal, gépeléssel szűrhető, Enterrel vagy kattintással vált (`git switch`, távoli ágnál `--track`). Ha a változások miatt nem lehet váltani, a git hibaüzenetét mutatja.
- **Pull request nézet:** a `⇄` kapcsolóval a repó fájllistája mindent mutat, amiben az ág a leágazási pont óta eltér (`git diff <merge-base>` + nem követett fájlok), a commitolatlanokkal együtt. Bekapcsolt állapotban a `⇄ develop` feliratra kattintva választható alapág, vagy kikapcsolható a nézet.
- **Repó jobbklikk:** +/− sorok (commitolatlan), lemaradás a távolihoz képest, „Commit…” (üzenetablak: *Commit* vagy *Push* = commit + push; Ctrl+Enter = Commit), és ha van pusholatlan commit vagy nincs upstream, „Push (N commit)”.
- **Fájl jobbklikk:** visszaállítás (megerősítéssel; új fájlnál törlés, átnevezettnél az eredeti visszaállítása), teljes útvonal másolása. Pull request nézetben a visszaállítás tiltott.

### Commitok (bal alsó blokk)
- Az aktív repó (amelyiknek a fájlját nézed, vagy amelyikre utoljára kattintottál) commitjai, 50-esével („Továbbiak betöltése”).
- Színezés: kék ● = csak ezen az ágon lévő commit, szürke = az alapágról jövő; ⬆ = még nincs pusholva; ⑂ = merge commit. A sor buboréka a teljes üzenetet, a szerzőt és a dátumot mutatja.
- Commitra kattintva lenyílnak a fájljai; fájlra kattintva írásvédett diff (a szülőhöz képest, merge-nél az első szülőhöz).
- A fejlécben `alap: … ▾`: az alapág kézzel átállítható (repónként és águnként megjegyzi), vagy „Automatikus felismerés”.

### Alapág-felismerés (`history.detect_base`)
1. `branch.<ág>.vscode-merge-base` a git configban (a VS Code menti, ha benne hozod létre az ágat).
2. Az ág reflogja: „branch: Created from X”; ha „Created from HEAD”, akkor a HEAD reflogjából az ág létrehozásakor elhagyott ág.
3. A legközelebbi ág: a develop/main/master és az utóbbi 180 nap ágai közül az, amelyikhez képest a legkevesebb saját commit van (`git rev-list --count <jelölt>..HEAD`). A saját upstream és a belőle leágazott ágak kimaradnak.
4. Kézi felülírás (a beállításfájlban).

### Diffnézet (jobb oldal)
- Monaco diff két oszlopban (⇆: egy oszlop), nyelv szerinti színezéssel. Megnyitáskor az első változásra ugrik; F7 / Shift+F7 = következő / előző változás.
- A jobb oldal szerkeszthető (a munkakönyvtár fájlja); Ctrl+S ment, a BOM-ot és a sorvégeket megtartja. A mentetlen állapotot ● jelzi.
- Automatikus frissítés: 1,5 mp-enként figyeli a repókat (`git status --porcelain=v2`) és a megnyitott fájlt; külső módosításnál újratölt, mentetlen szerkesztésnél rákérdez.
- Fájllistában a fel/le nyilakkal lehet lépkedni.

## Felépítés

| Fájl | Szerep |
|---|---|
| `LiteDiff.pyw` | Indító |
| `litediff/app.py` | pywebview-ablak, a JS felé nyitott `Api` osztály, háttérfigyelő, beállítások, ikon |
| `litediff/gitops.py` | Állapot (`status_full`), fájltartalom revízióból/lemezről, mentés, ágak, ágváltás |
| `litediff/gitops_actions.py` | Visszaállítás, statisztika, commit, push, vágólap |
| `litediff/history.py` | Alapág-felismerés, commitlista, commitfájlok, pull request lista |
| `litediff/web/` | `index.html`, `app.js`, `style.css`, ikon, `vendor/vs` (Monaco 0.58, MIT licenc) |
| `tools/make_icon.py` | Az ikon előállítása (Pillow) |
| `tests/` | pytest tesztek ideiglenes git repókkal |

- **Beállítások:** `%APPDATA%\LiteDiff\config.json` (repók, pull request nézet repónként, kézi alapágak). A felület apróságai (oszlopszélesség, összecsukott mappák, aktív repó) a WebView2 localStorage-ában vannak: `%APPDATA%\LiteDiff\webview`.
- **Memória:** a WebView2 GPU nélkül, egyetlen megjelenítő folyamattal fut (`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` az `app.py`-ban); induláskor törli a WebView2 HTTP-gyorsítótárát, hogy mindig a friss felület töltődjön be.

## Fejlesztés

- Függőségek telepítése: `py -3.12 -m venv .venv` → `.venv\Scripts\python.exe -m pip install -r requirements.txt`
- Tesztek: `.venv\Scripts\python.exe -m pytest -q tests` (32 teszt, ~1,5 perc; a git hívások miatt lassú)
- Ikon újrarajzolása: `.venv\Scripts\python.exe tools\make_icon.py`
- JS szintaxis-ellenőrzés: `node --check litediff\web\app.js`
- EXE készítése: `build.ps1` → `dist\LiteDiff.exe` (egyetlen fájl, ~15 MB; a WebView2 futtatókörnyezet a Windows 10/11 része)
