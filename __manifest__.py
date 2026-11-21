{
    "name": "Kontakty z ARES",
    "summary": "Název, adresa, IČO, DIČ, obor a zveřejněné bankovní účty kontaktu z ARES a registru plátců DPH",
    "description": """
Kontakty z ARES
===============

Po zadání IČO (nebo DIČ firmy) u nového kontaktu se z ARES hned ve formuláři doplní
název, adresa sídla, DIČ, IČO a obor podle převažující činnosti. Z registru plátců DPH
(Ministerstvo financí) se ověří, jestli není nespolehlivý plátce, a přidá se zveřejněný
bankovní účet. Uloží se až s kontaktem. Když je zveřejněných účtů víc, vybírají se po uložení.

U uloženého kontaktu je tlačítko **Aktualizovat z ARES** a v seznamu kontaktů akce
stejného jména: přepíše údaje podle registrů a změny zapíše do chatteru (staré → nové).
Jeden chybějící zveřejněný účet přidá rovnou, když jich je víc, nabídne okno s výběrem.
Název a BIC banky bere ze seznamu kódů bank ČNB, který jednou za měsíc stahuje.
""",
    "version": "20.0.1.1.0",
    "category": "Localization",
    "countries": ["cz"],
    "author": "Matouš Čmelík",
    "website": "https://github.com/cmeldas/l10n_cz_ares",
    "license": "LGPL-3",
    "depends": ["mail"],
    "data": [
        "security/ir.access.csv",
        "data/ir_cron.xml",
        "views/res_partner_views.xml",
        "wizard/ares_bank_wizard_views.xml",
    ],
    "installable": True,
}
