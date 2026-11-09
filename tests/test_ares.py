import re
from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests import Form, TransactionCase, tagged

from odoo.addons.l10n_cz_ares.tools import ares

CEZ = {
    "ico": "45274649",
    "obchodniJmeno": "ČEZ, a. s.",
    "sidlo": {
        "kodStatu": "CZ", "nazevObce": "Praha", "nazevMestskehoObvodu": "Praha 4", "nazevUlice": "Duhová",
        "cisloDomovni": 1444, "typCisloDomovni": 1, "cisloOrientacni": 2, "nazevCastiObce": "Michle",
        "psc": 14000, "textovaAdresa": "Duhová 1444/2, Michle, 14000 Praha 4", "standardizaceAdresy": True,
    },
    "dic": "CZ45274649",
    "seznamRegistraci": {"stavZdrojeDph": "AKTIVNI", "stavZdrojeSkDph": "NEEXISTUJICI"},
}
# Vymyšlený subjekt bez DPH na vesnici (IČO s platnou kontrolní číslicí).
WORKSHOP = {
    "ico": "12345679",
    "obchodniJmeno": "Truhlářství Pod Lesem s.r.o.",
    "sidlo": {
        "kodStatu": "CZ", "nazevObce": "Chožov", "nazevCastiObce": "Třtěno", "cisloDomovni": 21,
        "typCisloDomovni": 1, "psc": 44001, "textovaAdresa": "Třtěno 21, 44001 Chožov", "standardizaceAdresy": True,
    },
    "seznamRegistraci": {"stavZdrojeDph": "NEEXISTUJICI"},
}
KB_ACCOUNT = ("27", "1445680277", "0100")  # ČEZ, v registru i jako CZ4701000000271445680277
KB_IBAN = "CZ4701000000271445680277"
SK_IBAN = "SK2909000000005033845424"


def payer_xml(dic, status="NE", standard=(), other=()):
    accounts = "".join(
        f'<ucet datumZverejneni="2013-04-01"><standardniUcet predcisli="{p}" cislo="{n}" kodBanky="{b}"/></ucet>'
        for p, n, b in standard
    ) + "".join(f'<ucet datumZverejneni="2015-08-06"><nestandardniUcet cislo="{iban}"/></ucet>' for iban in other)
    return (
        '<?xml version="1.0" encoding="utf-8"?><soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">'
        '<soapenv:Body><StatusNespolehlivyPlatceRozsirenyResponse xmlns="http://adis.mfcr.cz/rozhraniCRPDPH/">'
        '<status odpovedGenerovana="2026-11-09" statusCode="0" statusText="OK"/>'
        f'<statusPlatceDPH dic="{dic}" nespolehlivyPlatce="{status}" cisloFu="13"><zverejneneUcty>{accounts}</zverejneneUcty>'
        "<nazevSubjektu>X</nazevSubjektu></statusPlatceDPH></StatusNespolehlivyPlatceRozsirenyResponse></soapenv:Body></soapenv:Envelope>"
    ).encode()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b""):
        self.status_code = status_code
        self.content = content
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("bez JSON")
        return self._payload


class FakeRegistries:
    """Podstrčené ARES, RES a registr plátců DPH; počítá dotazy."""

    def __init__(self, subjects, payers=None, nace=None, vat_down=False):
        self.subjects = subjects
        self.payers = payers or {}
        self.nace = nace or {}
        self.vat_down = vat_down
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append(url)
        if "/ekonomicke-subjekty-res/" in url:
            ico = url.rsplit("/", 1)[1]
            if ico not in self.nace:
                return FakeResponse(404, {"kod": "NENALEZENO"})
            return FakeResponse(200, {"zaznamy": [{"primarniZaznam": True, "czNacePrevazujici2008": self.nace[ico]}]})
        if "/ekonomicke-subjekty/" in url:
            ico = url.rsplit("/", 1)[1]
            if ico not in self.subjects:
                return FakeResponse(404, {"kod": "NENALEZENO", "popis": "Nenalezeno"})
            return FakeResponse(200, self.subjects[ico])
        if url == ares.VAT_REGISTRY_URL:
            if self.vat_down:
                raise ares.RegistryError("Server adisrws.mfcr.cz neodpovídá (ConnectTimeout). Zkus to za chvíli.")
            dic = re.search(r"<roz:dic>(\d+)</roz:dic>", kwargs["data"].decode()).group(1)
            return FakeResponse(200, content=self.payers.get(dic, payer_xml("0", "NE")))
        raise AssertionError(f"neočekávaný dotaz {url}")


@tagged("post_install", "-at_install")
class TestAresTools(TransactionCase):
    def test_ico(self):
        self.assertTrue(ares.is_valid_ico("45274649"))
        self.assertFalse(ares.is_valid_ico("45274648"))
        self.assertEqual(ares.normalize_ico(" 4527 4649 "), "45274649")
        self.assertEqual(ares.normalize_ico("CZ45274649"), "45274649")
        self.assertEqual(ares.normalize_ico("6947"), "00006947")
        for wrong in ("abc", "45274648", "CZ7757035572", "123456789"):
            with self.assertRaises(ValueError):
                ares.normalize_ico(wrong)
        self.assertEqual(ares.ico_from_vat("CZ 45274649"), "45274649")
        self.assertIsNone(ares.ico_from_vat("CZ7757035572"))
        self.assertEqual(ares.vat_digits("CZ699004572"), "699004572")

    def test_address(self):
        prague = ares.parse_address(CEZ["sidlo"])
        self.assertEqual(
            (prague["street"], prague["street2"], prague["city"], prague["zip"]),
            ("Duhová 1444/2", "Michle", "Praha 4", "140 00"),
        )
        village = ares.parse_address({"nazevObce": "Lhota", "nazevCastiObce": "Lhota", "cisloDomovni": 12,
                                      "typCisloDomovni": 1, "psc": 12345, "standardizaceAdresy": True})
        self.assertEqual((village["street"], village["street2"], village["city"]), ("č.p. 12", "", "Lhota"))
        self.assertEqual(ares.parse_address(WORKSHOP["sidlo"])["street"], "Třtěno 21")
        self.assertEqual(ares.parse_address(WORKSHOP["sidlo"])["city"], "Chožov")
        evidence = {"nazevObce": "Blansko", "nazevCastiObce": "Češkovice", "cisloDomovni": 45, "typCisloDomovni": 2}
        self.assertEqual(ares.parse_address(evidence)["street"], "Češkovice č.ev. 45")
        evidence_same = {"nazevObce": "Hlubočky", "nazevCastiObce": "Hlubočky", "cisloDomovni": 7, "typCisloDomovni": 2}
        self.assertEqual(ares.parse_address(evidence_same)["street"], "č.ev. 7")
        letter = {"nazevObce": "Brno", "nazevUlice": "Kounicova", "cisloDomovni": 10, "cisloOrientacni": 5,
                  "cisloOrientacniPismeno": "a", "nazevCastiObce": "Veveří"}
        self.assertEqual((ares.parse_address(letter)["street"], ares.parse_address(letter)["city"]), ("Kounicova 10/5a", "Brno"))
        text_only = ares.parse_address({"textovaAdresa": "Krakovany, okres Kolín, Česká republika", "standardizaceAdresy": False})
        self.assertEqual((text_only["street"], text_only["standardized"]), ("Krakovany, okres Kolín", False))

    def test_vat(self):
        self.assertEqual(ares.vat_from_subject(CEZ), ("CZ45274649", False))
        self.assertEqual(ares.vat_from_subject(WORKSHOP), (None, False))
        group_member = {"dic": "CZ00006947", "dicSkDph": "CZ699004572",
                        "seznamRegistraci": {"stavZdrojeDph": "ZANIKLY", "stavZdrojeSkDph": "AKTIVNI"}}
        self.assertEqual(ares.vat_from_subject(group_member), ("CZ699004572", True))

    def test_accounts(self):
        self.assertEqual(ares.czech_account_to_iban(*KB_ACCOUNT), KB_IBAN)
        self.assertEqual(ares.comparable_account("27-1445680277/0100"), KB_IBAN)
        self.assertEqual(ares.iban_to_domestic(KB_IBAN), "27-1445680277/0100")
        self.assertEqual(ares.iban_to_domestic("CZ6426000000002001268200"), "2001268200/2600")
        self.assertIsNone(ares.iban_to_domestic(SK_IBAN))
        nonstandard_cz = ares.parse_vat_payer(payer_xml("45274649", "NE", other=[KB_IBAN]), "45274649")
        self.assertEqual(nonstandard_cz["accounts"][0]["domestic"], "27-1445680277/0100")
        self.assertEqual(ares.comparable_account("CZ47 0100 0000 2714 4568 0277"), KB_IBAN)
        self.assertEqual(ares.czech_bank(KB_IBAN), ("Komerční banka, a.s.", "KOMBCZPP"))
        self.assertEqual(ares.czech_bank(SK_IBAN), (None, None))
        parsed = ares.parse_vat_payer(payer_xml("45274649", "ANO", [KB_ACCOUNT], [SK_IBAN]), "45274649")
        self.assertEqual(parsed["status"], "ANO")
        self.assertEqual([a["iban"] for a in parsed["accounts"]], [KB_IBAN, SK_IBAN])
        self.assertEqual(parsed["accounts"][0]["domestic"], "27-1445680277/0100")
        self.assertEqual(ares.parse_vat_payer(payer_xml("1", "NE"), "45274649"), {"status": "NENALEZEN", "accounts": []})
        with self.assertRaises(ares.RegistryError):
            ares.parse_vat_payer(b"<html>chyba</html>", "45274649")

    def test_nace_section(self):
        self.assertEqual(ares.nace_section("35110"), "D")
        self.assertEqual(ares.nace_section("01500"), "A")
        self.assertEqual(ares.nace_section("4711"), "G")
        self.assertIsNone(ares.nace_section("00"))
        self.assertIsNone(ares.nace_section(None))


@tagged("post_install", "-at_install")
class TestAresPartner(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Testovací IČO nesmí mít jiný kontakt v databázi, jinak se ozve upozornění na duplicitu.
        cls.env["res.partner"].with_context(active_test=False).search([]).filtered(
            lambda p: (p.additional_identifiers or {}).get("CZ_EN") in ("45274649", "12345679", "00006947")
        ).write({"additional_identifiers": False})
        cls.registries = FakeRegistries(
            {"45274649": CEZ, "12345679": WORKSHOP},
            payers={"45274649": payer_xml("45274649", "NE", [KB_ACCOUNT], [SK_IBAN])},
            nace={"45274649": "35110", "12345679": "01500"},
        )

    def patch_registries(self, registries=None):
        return patch.object(ares, "_request", side_effect=registries or self.registries)

    def test_new_partner_form(self):
        with self.patch_registries():
            form = Form(self.env["res.partner"])
            form.l10n_cz_ares_ico = "45274649"
            self.assertEqual(form.name, "ČEZ, a. s.")
            self.assertEqual(form.street, "Duhová 1444/2")
            self.assertEqual(form.street2, "Michle")
            self.assertEqual(form.city, "Praha 4")
            self.assertEqual(form.zip, "140 00")
            self.assertEqual(form.vat, "CZ45274649")
            partner = form.save()
        self.assertEqual(partner.country_id, self.env.ref("base.cz"))
        self.assertEqual(partner.additional_identifiers, {"CZ_EN": "45274649"})
        self.assertEqual(partner.industry_id, self.env.ref("base.res_partner_industry_D"))
        # dva zveřejněné účty: žádný se nepřidá sám, vybírají se po uložení v okně
        self.assertFalse(partner.bank_ids)
        other = self.env["res.partner"].create({"name": "Jiný kontakt", "bank_ids": [(0, 0, {"account_number": KB_IBAN})]})
        with self.patch_registries():
            action = partner.action_l10n_cz_ares_update()
        self.assertEqual(action["res_model"], "l10n_cz_ares.bank.wizard")
        wizard = self.env["l10n_cz_ares.bank.wizard"].browse(action["res_id"])
        self.assertIn("zveřejněných účtů k výběru: 2", wizard.summary)
        sk, kb = wizard.line_ids  # nejnověji zveřejněný nahoře
        self.assertEqual((sk.account_number, sk.display_number), ("SK29 0900 0000 0050 3384 5424",) * 2)
        self.assertTrue(sk.description.startswith("Zahraniční účet ("))
        self.assertIn("naposledy zveřejněný", sk.description)
        self.assertEqual((kb.display_number, kb.bank_name, kb.published_on.isoformat()), ("27-1445680277/0100", "Komerční banka, a.s.", "2013-04-01"))
        self.assertTrue(kb.description.startswith("Tuzemský účet; v Odoo ho má i "))
        self.assertIn(other.display_name, kb.description)
        self.assertNotIn("naposledy", kb.description)
        kb.selected = True
        wizard.action_import()
        self.assertEqual(partner.bank_ids.mapped("account_number"), ["CZ47 0100 0000 2714 4568 0277"])
        bank = partner.bank_ids
        self.assertEqual((bank.bank_name, bank.bank_bic), ("Komerční banka, a.s.", "KOMBCZPP"))
        self.assertIn("27-1445680277/0100", bank.note)
        self.assertFalse(bank.allow_out_payment)
        self.assertIn("CZ47 0100", partner.message_ids[0].body)
        # zbývá jeden účet, ten se při další aktualizaci přidá rovnou
        with self.patch_registries():
            action = partner.action_l10n_cz_ares_update()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(len(partner.bank_ids), 2)

    def test_wizard_import_all(self):
        partner = self.env["res.partner"].create({"name": "ČEZ", "additional_identifiers": {"CZ_EN": "45274649"}})
        with self.patch_registries():
            action = partner.action_l10n_cz_ares_update()
        wizard = self.env["l10n_cz_ares.bank.wizard"].browse(action["res_id"])
        self.assertIn("výběr v okně", partner.message_ids[0].body)
        result = wizard.action_import_all()
        self.assertEqual(result["params"]["message"], "Přidány 2 bankovní účty.")
        self.assertEqual(len(partner.bank_ids), 2)
        self.assertEqual(self.env["l10n_cz_ares.bank.wizard"].browse(action["res_id"]).action_import()["params"]["message"],
                         "Žádný účet nepřidán.")

    def test_new_partner_single_account(self):
        registries = FakeRegistries({"45274649": CEZ}, payers={"45274649": payer_xml("45274649", "NE", [KB_ACCOUNT])})
        with self.patch_registries(registries):
            form = Form(self.env["res.partner"])
            form.l10n_cz_ares_ico = "45274649"
            partner = form.save()
        self.assertEqual(partner.bank_ids.mapped("account_number"), ["CZ47 0100 0000 2714 4568 0277"])
        self.assertTrue(any("CZ47 0100" in message.body for message in partner.message_ids))

    def test_bulk_leaves_choice_for_later(self):
        cez = self.env["res.partner"].create({"name": "ČEZ", "additional_identifiers": {"CZ_EN": "45274649"}})
        workshop = self.env["res.partner"].create({"name": "Truhlářství", "additional_identifiers": {"CZ_EN": "12345679"}})
        with self.patch_registries():
            action = (cez | workshop).action_l10n_cz_ares_update()
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("Víc zveřejněných účtů", action["params"]["message"])
        self.assertFalse(cez.bank_ids)
        self.assertIn("vyber je tlačítkem Aktualizovat z ARES", cez.message_ids[0].body)

    def test_pending_accounts_are_sanitized(self):
        """Účty z formuláře: jen číslo, banka a poznámka; povolení odchozích plateb nikdy."""
        partner = self.env["res.partner"].create({
            "name": "Firma",
            "l10n_cz_ares_pending_accounts": [
                {"account_number": "27-1445680277/0100", "allow_out_payment": True, "company_id": 1},
                "nesmysl",
                {"account_number": KB_IBAN},  # tentýž účet jinak zapsaný
            ],
        })
        self.assertEqual(partner.bank_ids.mapped("account_number"), ["27-1445680277/0100"])
        self.assertFalse(partner.bank_ids.allow_out_payment)
        self.assertFalse(partner.bank_ids.company_id)

    def test_new_partner_from_vat(self):
        with self.patch_registries():
            form = Form(self.env["res.partner"])
            form.vat = "CZ45274649"
            self.assertEqual(form.l10n_cz_ares_ico, "45274649")
            self.assertEqual(form.name, "ČEZ, a. s.")

    def test_new_partner_not_vat_payer(self):
        registries = FakeRegistries({"12345679": WORKSHOP}, nace={"12345679": "01500"})
        with self.patch_registries(registries):
            form = Form(self.env["res.partner"])
            form.l10n_cz_ares_ico = "12345679"
            partner = form.save()
        self.assertEqual((partner.name, partner.street, partner.city), ("Truhlářství Pod Lesem s.r.o.", "Třtěno 21", "Chožov"))
        self.assertFalse(partner.vat)
        self.assertFalse(partner.bank_ids)
        self.assertEqual(partner.industry_id, self.env.ref("base.res_partner_industry_A"))
        self.assertFalse(any(ares.VAT_REGISTRY_URL == url for url in registries.calls))

    def test_unknown_ico_keeps_form(self):
        with self.patch_registries():
            form = Form(self.env["res.partner"])
            form.name = "Ručně"
            form.l10n_cz_ares_ico = "00006947"  # platné IČO, v podstrčeném ARES není
            self.assertEqual(form.name, "Ručně")
            self.assertFalse(form.street)

    def test_update_existing_partner(self):
        partner = self.env["res.partner"].create({
            "name": "ČEZ",
            "street": "Stará 1",
            "street2": "Někde",
            "country_id": self.env.ref("base.cz").id,
            "additional_identifiers": {"CZ_EN": "45274649"},
            "bank_ids": [(0, 0, {"account_number": "27-1445680277/0100"})],
        })
        with self.patch_registries():
            action = partner.action_l10n_cz_ares_update()
        self.assertEqual(action["params"]["type"], "success")
        self.assertEqual((partner.name, partner.street, partner.street2), ("ČEZ, a. s.", "Duhová 1444/2", "Michle"))
        self.assertEqual(partner.vat, "CZ45274649")
        # účet v tuzemském tvaru je tentýž jako zveřejněný IBAN, přibude jen slovenský
        self.assertEqual(len(partner.bank_ids), 2)
        self.assertEqual(partner.bank_ids[1].account_number, "SK29 0900 0000 0050 3384 5424")
        body = partner.message_ids[0].body
        self.assertIn("Název: ČEZ → ČEZ, a. s.", body)
        self.assertIn("SK29 0900", body)
        self.assertIn("spolehlivý plátce DPH", body)

        with self.patch_registries():
            action = partner.action_l10n_cz_ares_update()
        self.assertIn("Údaje odpovídají ARES", action["params"]["message"])
        self.assertEqual(len(partner.bank_ids), 2)

    def test_update_keeps_own_industry(self):
        own = self.env.ref("base.res_partner_industry_G")
        partner = self.env["res.partner"].create({"name": "ČEZ", "vat": "CZ45274649", "industry_id": own.id,
                                                  "country_id": self.env.ref("base.cz").id})
        with self.patch_registries():
            partner.action_l10n_cz_ares_update()
        self.assertEqual(partner.industry_id, own)
        self.assertEqual(partner.additional_identifiers, {"CZ_EN": "45274649"})  # IČO z DIČ

    def test_warnings(self):
        ceased = dict(WORKSHOP, datumZaniku="2024-12-31")
        registries = FakeRegistries(
            {"45274649": CEZ, "12345679": ceased},
            payers={"45274649": payer_xml("45274649", "ANO", [KB_ACCOUNT])},
        )
        cez = self.env["res.partner"].create({"name": "ČEZ", "additional_identifiers": {"CZ_EN": "45274649"}})
        workshop = self.env["res.partner"].create({"name": "Truhlářství", "vat": "CZ12345679",
                                               "additional_identifiers": {"CZ_EN": "12345679"}})
        with self.patch_registries(registries):
            action = cez.action_l10n_cz_ares_update()
            self.assertEqual(action["params"]["type"], "warning")
            self.assertIn("NESPOLEHLIVÝ PLÁTCE", cez.message_ids[0].body)
            workshop.action_l10n_cz_ares_update()
        self.assertIn("zanikl 31. 12. 2024", workshop.message_ids[0].body)
        self.assertIn("není plátcem DPH, kontakt má ale DIČ", workshop.message_ids[0].body)
        self.assertEqual(workshop.vat, "CZ12345679")

    def test_vat_registry_down(self):
        registries = FakeRegistries({"45274649": CEZ}, vat_down=True)
        partner = self.env["res.partner"].create({"name": "ČEZ", "additional_identifiers": {"CZ_EN": "45274649"}})
        with self.patch_registries(registries):
            action = partner.action_l10n_cz_ares_update()
        self.assertEqual(partner.name, "ČEZ, a. s.")
        self.assertFalse(partner.bank_ids)
        self.assertEqual(action["params"]["type"], "warning")
        self.assertIn("Bankovní účty nenačteny", partner.message_ids[0].body)

    def test_errors_single_and_bulk(self):
        without_ico = self.env["res.partner"].create({"name": "Bez IČO"})
        unknown = self.env["res.partner"].create({"name": "Neznámý", "additional_identifiers": {"CZ_EN": "00006947"}})
        cez = self.env["res.partner"].create({"name": "ČEZ", "additional_identifiers": {"CZ_EN": "45274649"}})
        with self.patch_registries():
            with self.assertRaisesRegex(UserError, "nemá IČO"):
                without_ico.action_l10n_cz_ares_update()
            with self.assertRaisesRegex(UserError, "v ARES není"):
                unknown.action_l10n_cz_ares_update()
            action = (without_ico | unknown | cez).action_l10n_cz_ares_update()
        self.assertIn("Aktualizováno 1 z 3", action["params"]["message"])
        self.assertIn("Nepovedlo se", action["params"]["message"])
        self.assertEqual(cez.name, "ČEZ, a. s.")
        self.assertIn("v ARES není", unknown.message_ids[0].body)

    def test_duplicate_warning(self):
        self.env["res.partner"].create({"name": "ČEZ starý", "additional_identifiers": {"CZ_EN": "45274649"}})
        partner = self.env["res.partner"].new({"name": "x"})
        with self.patch_registries():
            result = partner._l10n_cz_ares_lookup("45274649")
        self.assertTrue(any("ČEZ starý" in warning for warning in partner._l10n_cz_ares_warnings(result)))

    def test_child_contact_not_updated(self):
        company = self.env["res.partner"].create({"name": "Firma"})
        person = self.env["res.partner"].create({"name": "Osoba", "parent_id": company.id})
        with self.assertRaises(UserError):
            person.action_l10n_cz_ares_update()
