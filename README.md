# Kontakty z ARES (`l10n_cz_ares`)

Modul pro Odoo 20. Po zadání IČO doplní kontakt z veřejných registrů: název, adresu sídla, IČO,
DIČ, obor a zveřejněné bankovní účty.

## Jak se používá

**Nový kontakt.** Pod polem firmy (Company Employer) je pole **IČO**. Po zadání IČO a odchodu
z pole se formulář vyplní hned, uloží se až s kontaktem. Doplnění spustí i DIČ firmy
(CZ + 8 číslic) zapsané do pole DIČ. Když všechno proběhne v pořádku, ukáže se krátké shrnutí.
Když se něco nepovede nebo je potřeba upozornit (viz níže), otevře se okno. Má-li firma
v registru plátců DPH jediný zveřejněný účet, přibude v kartě Fakturace po uložení, protože
Odoo 20 účty u neuloženého kontaktu přidat nedovolí. Zapíše se i do chatteru. Když má účtů víc,
okno to oznámí a účty se vyberou po uložení tlačítkem Aktualizovat z ARES.

Pole IČO nahoře se ukazuje jen u firem (ne u kontaktních osob), které ještě nemají uložené IČO,
a jen když země není vyplněná nebo je to Česko. Uložené IČO je potom mezi identifikátory u DIČ.

**Uložený kontakt.** Pod identifikátory je tlačítko **Aktualizovat z ARES**. Bere IČO
z identifikátorů, a když chybí, odvodí ho z DIČ firmy. Údaje přepíše podle registrů a do chatteru
zapíše, co se změnilo (staré → nové). Chybí-li kontaktu jediný zveřejněný účet, přidá ho rovnou
a hláška nahoře ukáže krátké shrnutí. Chybí-li jich víc, otevře okno **Bankovní účty z registru
plátců DPH**:

- nahoře výsledek aktualizace a upozornění,
- seznam chybějících účtů, nejnověji zveřejněný nahoře: přepínač Přidat, číslo účtu (české
  v tuzemském tvaru `27-1445680277/0100`, zahraniční jako IBAN), banka, datum zveřejnění a popis,
- tlačítka Přidat vybrané, Přidat všechny a Nepřidávat.

Registr plátců DPH u účtů neuvádí, k čemu slouží, a jinde to veřejně není. Popis proto říká jen
to, co se o účtu ví: tuzemský nebo zahraniční (země podle IBAN), naposledy zveřejněný, a jestli
stejný účet v Odoo nemá jiný kontakt. Vybírá se podle faktur nebo smlouvy s dodavatelem.
Nepřidané účty okno nabídne znovu při další aktualizaci.

**Víc kontaktů najednou.** V seznamu kontaktů vyber kontakty a zvol **Akce → Aktualizovat
z ARES**. Chyba u jednoho kontaktu (chybí IČO, IČO v ARES není) ostatní nezastaví a zapíše se
mu do chatteru. Jediný chybějící účet se přidá, víc účtů se nepřidá: hláška a chatter řeknou,
že je vybereš tlačítkem na kontaktu.

## Co se doplní a odkud

| pole | zdroj | poznámka |
|---|---|---|
| Název | ARES, obchodní jméno | |
| Ulice, Ulice 2, Město, PSČ, Země | ARES, sídlo | tvar jako v ARES: „Duhová 1444/2“ a část obce „Michle“ do Ulice 2; bez ulice „Třtěno 114“ nebo „č.p. 337“, u čísla evidenčního „č.ev.“; v Praze je město obvod („Praha 4“) |
| IČO | identifikátor `CZ_EN` | Odoo 20 nemá pole IČO (`company_registry`), IČO je mezi identifikátory |
| DIČ | ARES | jen u plátce DPH nebo identifikované osoby; člen skupiny DPH dostane DIČ skupiny |
| Obor | ARES, Registr ekonomických subjektů, převažující činnost CZ-NACE | sekce A–U jako obory Odoo; vyplněný obor se nepřepisuje |
| Bankovní účty | registr plátců DPH (MF), zveřejněné účty | IBAN, banka a BIC podle kódu banky ČNB, poznámka s datem zveřejnění a tuzemským číslem |

Registr plátců DPH vede jen plátce, takže kdo plátcem DPH není, zveřejněné účty nemá. Velké firmy
mají v registru účtů hodně (ČEZ 17, Alza 19), ty se vybírají v okně. Nové účty nemají zapnuté
„Odesílat peníze“ (důvěryhodný účet). To podle ochrany proti podvodům v Odoo zapíná člověk
s oprávněním, až si účet ověří. U každého založeného účtu zapíše Odoo do chatteru vlastní záznam
„Bank Account … created“.

Účet, který už kontakt má (i v tuzemském zápisu `27-1445680277/0100`), se znovu nepřidá. Účty se
nikdy nemažou a nepřidávají se u kontaktu vlastní firmy, ani když uživatel nemá právo účty zakládat.

## Upozornění

Při doplnění ve formuláři se ukážou v okně, při aktualizaci tučně v chatteru:

- subjekt podle ARES zanikl (datum zániku),
- **nespolehlivý plátce DPH**,
- registr plátců DPH neodpověděl, takže účty nejsou načtené (kontakt se doplní i tak),
- adresa v ARES není rozepsaná na ulici a obec, je celá v poli Ulice,
- podle ARES není plátcem DPH, ale kontakt má DIČ (DIČ se nemaže),
- stejné IČO už má jiná firma.

## Co se nemění

Kontaktní osoby firmy (s vyplněnou firmou) se z ARES neaktualizují, adresu přebírají od firmy.
DIČ se nemaže, vyplněný obor se nepřepisuje. Typ kontaktu (firma nebo osoba) se nenastavuje.
V Odoo 20 je to počítané pole: firma je, kdo má DIČ.

## Technika

- ARES REST API `https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/ekonomicke-subjekty/{ico}` a
  `…/ekonomicke-subjekty-res/{ico}` (převažující činnost `czNacePrevazujici2008`).
- Registr plátců DPH: SOAP `getStatusNespolehlivyPlatceRozsireny` na
  `https://adisrws.mfcr.cz/dpr/axis2/services/rozhraniCRPDPH.rozhraniCRPDPHSOAP`, dává stav
  spolehlivosti a zveřejněné účty (standardní předčíslí-číslo/kód, nestandardní jako IBAN).
- Volání mají timeout 10 s, bez přihlášení. Na jedno IČO jdou tři dotazy (ARES, RES, registr DPH).
- `data/cnb_bank_codes.csv`: kódy bank ČNB (kód, název, BIC), stažené 9. 11. 2026 z
  `https://www.cnb.cz/cs/platebni-styk/.galleries/ucty_kody_bank/download/kody_bank_CR.csv`.
  Neznámý kód banky nevadí, účet se založí bez názvu banky.
- Čisté funkce pro registry jsou v `tools/ares.py`, napojení na kontakt v `models/res_partner.py`,
  okno s výběrem účtů v `wizard/ares_bank_wizard.py` (dočasné modely `l10n_cz_ares.bank.wizard`
  a `.line`, právo pro interní uživatele). Jediný účet z doplnění ve formuláři čeká v pomocném poli
  `l10n_cz_ares_pending_accounts` a založí se při uložení. Z hodnot formuláře i okna se berou jen
  číslo účtu, banka a poznámka.
- `models/ir_ui_view.py` obchází past Odoo 20 při aktualizaci modulu, který nezávisí na `website`,
  v databázi s webem: sloupec `ir_ui_view.visibility` je NOT NULL bez výchozí hodnoty a nové
  pohledy modulu by se při `-u` nevložily. `init()` mu nastaví výchozí hodnotu `public` jako v Odoo.

## Instalace

Složku `l10n_cz_ares` dát do cesty s addony (`addons_path`), restartovat Odoo a modul nainstalovat
v Aplikacích (Aktualizovat seznam aplikací, vyhledat „ARES“), nebo z příkazové řádky:

```sh
odoo -d <databáze> -i l10n_cz_ares --stop-after-init
```

Aktualizace po nové verzi stejně s `-u l10n_cz_ares`. Server musí mít přístup na `ares.gov.cz`
a `adisrws.mfcr.cz` (HTTPS), registry přihlášení nepotřebují.

## Testy

Testy podstrkují odpovědi registrů, do sítě nechodí. Nejlépe v čisté databázi:

```sh
odoo -d <testovací databáze> -i contacts,l10n_cz,l10n_cz_ares --without-demo=all \
  --test-enable --test-tags /l10n_cz_ares --stop-after-init --no-http
```

## Podobné moduly

Pro Odoo 20 hotový modul nebyl (9. 11. 2026). Pro Odoo 19 je zdarma `ash_cz_ares` od Ash technology
(GitHub `ash-technology/l10n_cz_extra`, LGPL-3): IČO, název, adresa, DIČ, skupina DPH a stav
spolehlivosti v samostatném poli, bez bankovních účtů a oboru. Placený `do_ares` (Dataobjects,
Odoo 18 a 19) kontroluje partnery při účtování.
