import logging
import re

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Command
from odoo.tools import SQL
from odoo.tools.bank_account_number import format_account_number

from ..tools import ares

_logger = logging.getLogger(__name__)

ICO_KEY = "CZ_EN"  # IČO v `additional_identifiers` (Odoo 20)
PAYER_STATUS = {
    "NE": "spolehlivý plátce DPH",
    "ANO": "NESPOLEHLIVÝ PLÁTCE DPH",
    "NENALEZEN": "v registru plátců DPH nenalezen",
}
FIELD_LABELS = {
    "name": "Název",
    "street": "Ulice",
    "street2": "Ulice 2",
    "city": "Město",
    "zip": "PSČ",
    "country_id": "Země",
    "vat": "DIČ",
    "industry_id": "Obor",
}


def _format_date(value: str | None) -> str:
    """Datum z registru (RRRR-MM-DD) česky („1. 4. 2013“)."""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value or "")
    return f"{int(match[3])}. {int(match[2])}. {match[1]}" if match else (value or "")


class ResPartner(models.Model):
    _inherit = "res.partner"

    l10n_cz_ares_ico = fields.Char(
        string="IČO",
        compute="_compute_l10n_cz_ares_ico",
        inverse="_inverse_l10n_cz_ares_ico",
        help="Po zadání IČO se z ARES doplní název, adresa, DIČ a obor a z registru plátců DPH "
             "zveřejněné bankovní účty. Uloží se jako identifikátor IČO.",
    )
    l10n_cz_ares_show_lookup = fields.Boolean(compute="_compute_l10n_cz_ares_show_lookup")
    # Účty z doplnění ve formuláři se zakládají až při uložení: pole bankovních účtů je v Odoo 20
    # u nového kontaktu jen pro čtení a bez účetních práv ve formuláři vůbec není.
    l10n_cz_ares_pending_accounts = fields.Json(
        compute="_compute_l10n_cz_ares_pending_accounts",
        inverse="_inverse_l10n_cz_ares_pending_accounts",
    )

    @api.depends("additional_identifiers")
    def _compute_l10n_cz_ares_ico(self):
        for partner in self:
            partner.l10n_cz_ares_ico = (partner.additional_identifiers or {}).get(ICO_KEY) or False

    def _inverse_l10n_cz_ares_ico(self):
        for partner in self.filtered("l10n_cz_ares_ico"):
            try:
                ico = ares.normalize_ico(partner.l10n_cz_ares_ico)
            except ValueError as error:
                raise ValidationError(str(error)) from error
            identifiers = partner.additional_identifiers or {}
            if identifiers.get(ICO_KEY) != ico:
                partner.additional_identifiers = {**identifiers, ICO_KEY: ico}

    @api.depends("parent_id", "additional_identifiers")
    def _compute_l10n_cz_ares_show_lookup(self):
        """Pole IČO nahoře ve formuláři jen u firem bez uloženého IČO; uložené je v identifikátorech."""
        for partner in self:
            saved = (partner._origin.additional_identifiers or {}) if partner._origin else {}
            partner.l10n_cz_ares_show_lookup = not partner.parent_id and not saved.get(ICO_KEY)

    def _compute_l10n_cz_ares_pending_accounts(self):
        self.l10n_cz_ares_pending_accounts = False

    def _inverse_l10n_cz_ares_pending_accounts(self):
        """Založí účet, který doplnění z ARES ve formuláři nachystalo, a zapíše ho do chatteru."""
        for partner in self.filtered("l10n_cz_ares_pending_accounts"):
            if partner.parent_id or partner._l10n_cz_ares_accounts_blocked():
                continue
            accounts = [a for a in partner.l10n_cz_ares_pending_accounts if isinstance(a, dict)]
            banks = partner._l10n_cz_ares_create_banks(accounts)
            if banks:
                partner._l10n_cz_ares_post_banks(banks)

    def _l10n_cz_ares_create_banks(self, accounts):
        """Založí účty kontaktu z hodnot (číslo, banka, BIC, poznámka); ty, které už má, přeskočí.

        Z hodnot se bere jen tohle, „Odesílat peníze“ zůstává vypnuté (účet ověřuje člověk).
        """
        self.ensure_one()
        existing = {ares.comparable_account(bank.account_number) for bank in self.bank_ids}
        values = []
        for account in accounts:
            number = str(account.get("account_number") or "")
            key = ares.comparable_account(number)
            if not number or key in existing:
                continue
            existing.add(key)
            values.append({
                **{field: account.get(field) or False for field in ("bank_name", "bank_bic", "note")},
                "account_number": number,
                "partner_id": self.id,
                "allow_out_payment": False,
            })
        return self.env["res.partner.bank"].create(values)

    def _l10n_cz_ares_post_banks(self, banks):
        items = Markup().join(Markup("<li>%s</li>") % bank.display_name for bank in banks)
        self.message_post(body=Markup("<p>Z ARES přidané účty zveřejněné v registru plátců DPH:</p><ul>%s</ul>") % items)

    # ------------------------------------------------------------ formulář: doplnění při zadání

    @api.onchange("l10n_cz_ares_ico")
    def _onchange_l10n_cz_ares_ico(self):
        """Zadané IČO doplní kontakt z ARES hned ve formuláři; uloží se až s kontaktem."""
        typed = (self.l10n_cz_ares_ico or "").strip()
        saved = (self._origin.additional_identifiers or {}).get(ICO_KEY) if self._origin else None
        if not typed or typed == saved:
            return None
        try:
            ico = ares.normalize_ico(typed)
            result = self._l10n_cz_ares_lookup(ico)
        except (ValueError, ares.RegistryError) as error:
            return {"warning": {"title": "ARES", "message": str(error), "type": "dialog"}}
        accounts = self._l10n_cz_ares_fill_form(result)
        warnings = self._l10n_cz_ares_warnings(result)
        if len(accounts) > 1:
            summary = self._l10n_cz_ares_summary(result, to_pick=len(accounts))
        else:
            summary = self._l10n_cz_ares_summary(result, added=len(accounts))
        if warnings or len(accounts) > 1:
            return {"warning": {"title": "ARES", "message": "\n\n".join([*warnings, summary]), "type": "dialog"}}
        return {"warning": {"title": "ARES", "message": summary, "type": "notification"}}

    @api.onchange("vat")
    def _onchange_vat_l10n_cz_ares(self):
        """DIČ firmy (CZ + IČO) zadané u kontaktu bez IČO spustí doplnění z ARES."""
        if self.l10n_cz_ares_show_lookup and not self.l10n_cz_ares_ico:
            ico = ares.ico_from_vat(self.vat)
            if ico:
                self.l10n_cz_ares_ico = ico

    def _l10n_cz_ares_fill_form(self, result):
        """Údaje z ARES do formuláře (v paměti); vrátí zveřejněné účty, které kontakt nemá.

        Jediný účet se založí při uložení. Víc účtů ne: z formuláře nového kontaktu okno
        s výběrem otevřít nejde, vybírají se po uložení tlačítkem Aktualizovat z ARES.
        """
        values = self._l10n_cz_ares_values(result)
        if self.industry_id:
            values.pop("industry_id", None)
        self.update(values)
        self.additional_identifiers = {**(self.additional_identifiers or {}), ICO_KEY: result["ico"]}
        accounts = self._l10n_cz_ares_new_accounts(result) if not self._l10n_cz_ares_accounts_blocked() else []
        pending = [self._l10n_cz_ares_bank_values(accounts[0])] if len(accounts) == 1 else False
        self.l10n_cz_ares_pending_accounts = pending
        return accounts

    # ------------------------------------------------------------ tlačítko a hromadná akce

    def action_l10n_cz_ares_update(self):
        """Aktualizuje kontakty z ARES a registru plátců DPH; podrobnosti zapíše do chatteru.

        Jeden kontakt: chyba se ukáže jako hláška a nic se nezmění. Víc kontaktů (akce v seznamu):
        chyba u jednoho ostatní nezastaví, zapíše se mu do chatteru.
        """
        partners = self.filtered(lambda p: not p.parent_id)
        if not partners:
            raise UserError("Kontaktní osoby firmy se z ARES neaktualizují, otevři firmu.")
        single = len(partners) == 1
        updated, with_warnings, failed, picking = (partners.browse(),) * 4
        summary = ""
        for partner in partners:
            try:
                with self.env.cr.savepoint():
                    ico = partner._l10n_cz_ares_current_ico()
                    if not ico:
                        raise UserError("Kontakt nemá IČO ani DIČ firmy (CZ + 8 číslic). Doplň IČO v identifikátorech.")
                    result = partner._l10n_cz_ares_lookup(ico)
                    changes, banks, to_pick = partner._l10n_cz_ares_update_record(result)
            except (UserError, ares.RegistryError) as error:
                if single:
                    raise UserError(str(error)) from error
                partner.message_post(body=Markup("<p>Aktualizace z ARES se nepovedla: %s</p>") % str(error))
                failed |= partner
                continue
            warnings = partner._l10n_cz_ares_warnings(result)
            partner._l10n_cz_ares_post_message(result, changes, banks, warnings, len(to_pick), single)
            updated |= partner
            if warnings:
                with_warnings |= partner
            if to_pick:
                picking |= partner
            summary = partner._l10n_cz_ares_summary(result, len(banks), len(to_pick), changes)
            if single and to_pick:
                return partner._l10n_cz_ares_open_bank_wizard(to_pick, [*warnings, summary])
        if single:
            message = summary + (" Pozor, upozornění v chatteru." if with_warnings else "")
        else:
            message = f"Aktualizováno {len(updated)} z {len(partners)}."
            if with_warnings:
                message += f" S upozorněním: {', '.join(with_warnings.mapped('display_name'))}."
            if failed:
                message += f" Nepovedlo se: {', '.join(failed.mapped('display_name'))}."
            if picking:
                message += (f" Víc zveřejněných účtů (vyber je tlačítkem na kontaktu): "
                            f"{', '.join(picking.mapped('display_name'))}.")
            message += " Podrobnosti jsou v chatteru kontaktů."
        problems = bool(with_warnings or failed)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "ARES",
                "message": message,
                "type": "warning" if problems else "success",
                "sticky": problems,
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def _l10n_cz_ares_current_ico(self):
        ico = (self.additional_identifiers or {}).get(ICO_KEY)
        if ico:
            try:
                return ares.normalize_ico(ico)
            except ValueError as error:
                raise UserError(str(error)) from error
        return ares.ico_from_vat(self.vat)

    def _l10n_cz_ares_update_record(self, result):
        """Zapíše rozdíly proti ARES a doplní zveřejněné účty.

        Jediný chybějící účet se založí rovnou, víc jich vybere uživatel v okně.
        :return: (změny, založené účty, účty k výběru)
        """
        values = self._l10n_cz_ares_values(result)
        if self.industry_id:
            values.pop("industry_id", None)
        changes, to_write = [], {}
        for name, new in values.items():
            old = self[name]
            if name == "vat":
                same = re.sub(r"\W", "", old or "").upper() == re.sub(r"\W", "", new or "").upper()
            else:
                same = (old or False) == (new or False)
            if not same:
                changes.append((name, old, new))
                to_write[name] = new.id if isinstance(new, models.BaseModel) else new
        identifiers = self.additional_identifiers or {}
        if identifiers.get(ICO_KEY) != result["ico"]:
            changes.append(("ico", identifiers.get(ICO_KEY), result["ico"]))
            to_write["additional_identifiers"] = {**identifiers, ICO_KEY: result["ico"]}
        if to_write:
            self.write(to_write)
        accounts = self._l10n_cz_ares_new_accounts(result) if not self._l10n_cz_ares_accounts_blocked() else []
        if len(accounts) > 1:
            return changes, self.env["res.partner.bank"], accounts
        return changes, self._l10n_cz_ares_create_banks([self._l10n_cz_ares_bank_values(a) for a in accounts]), []

    def _l10n_cz_ares_open_bank_wizard(self, accounts, messages):
        """Okno s výběrem zveřejněných účtů, které kontakt nemá."""
        self.ensure_one()
        newest_first = sorted(accounts, key=lambda a: a["published_on"] or "", reverse=True)
        wizard = self.env["l10n_cz_ares.bank.wizard"].create({
            "partner_id": self.id,
            "summary": "\n".join(messages),
            "line_ids": [Command.create(self._l10n_cz_ares_wizard_line_values(a, accounts)) for a in newest_first],
        })
        return {
            "type": "ir.actions.act_window",
            "name": "Bankovní účty z registru plátců DPH",
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
            "context": {"dialog_size": "extra-large"},
        }

    def _l10n_cz_ares_wizard_line_values(self, account, accounts):
        """Řádek okna s výběrem: číslo, banka a popis z toho, co o účtu víme.

        Registr plátců DPH u účtů neuvádí, k čemu slouží. Popis proto říká jen, jestli je účet
        tuzemský nebo z jaké země, jestli je nejnověji zveřejněný a jestli ho v Odoo nemá jiný kontakt.
        """
        values = self._l10n_cz_ares_bank_values(account)
        iban = account["iban"]
        country = self.env["res.country"].search([("code", "=", iban[:2])], limit=1) if iban[:2].isalpha() else None
        if iban.startswith("CZ"):
            notes = ["tuzemský účet"]
        elif country:
            notes = [f"zahraniční účet ({country.name})"]
        else:
            notes = ["účet v nestandardním tvaru"]
        latest = max((a["published_on"] or "" for a in accounts), default="")
        if account["published_on"] and account["published_on"] == latest:
            notes.append("naposledy zveřejněný")
        others = self.env["res.partner.bank"].search([
            ("account_number", "=", values["account_number"]),
            ("partner_id", "!=", self.id),
        ], limit=3).partner_id
        if others:
            notes.append(f"v Odoo ho má i {', '.join(others.mapped('display_name'))}")
        description = "; ".join(notes)
        return {
            **values,
            "display_number": account["domestic"] or values["account_number"],
            "published_on": account["published_on"] or False,
            "description": description[:1].upper() + description[1:],
        }

    # ------------------------------------------------------------ společné

    def _l10n_cz_ares_lookup(self, ico):
        """Data z ARES, registru plátců DPH a RES pro jedno IČO.

        ARES je povinný (chyba se vyhodí), registr plátců DPH a převažující činnost volitelné:
        když neodpoví, kontakt se doplní bez účtů nebo oboru a uživatel dostane upozornění.
        """
        subject = ares.parse_subject(ares.fetch_subject(ico))
        if not subject["name"]:
            raise ares.RegistryError(f"ARES u IČO {ico} nevrátil název subjektu.")
        result = {"ico": ico, "subject": subject, "payer": None, "payer_error": None, "industry": None}
        dic = ares.vat_digits(subject["vat"])
        if dic:
            try:
                result["payer"] = ares.fetch_vat_payer(dic)
            except ares.RegistryError as error:
                result["payer_error"] = str(error)
        try:
            section = ares.nace_section(ares.fetch_prevailing_nace(ico))
        except ares.RegistryError as error:
            _logger.info("Převažující činnost IČO %s z ARES nenačtena: %s", ico, error)
            section = None
        if section:
            result["industry"] = self.env.ref(f"base.res_partner_industry_{section}", raise_if_not_found=False)
        return result

    def _l10n_cz_ares_values(self, result):
        """Hodnoty polí kontaktu z výsledku dotazu (prázdné údaje ARES nic nemažou kromě ulice 2)."""
        subject = result["subject"]
        address = subject["address"]
        values = {
            "name": subject["name"],
            "street": address["street"] or False,
            "street2": address["street2"] or False,
            "city": address["city"] or False,
            "zip": address["zip"] or False,
        }
        values = {key: value for key, value in values.items() if value or key == "street2"}
        country = self.env["res.country"].search([("code", "=", address["country_code"])], limit=1)
        if country:
            values["country_id"] = country
        if subject["vat"]:
            values["vat"] = subject["vat"]
        if result["industry"]:
            values["industry_id"] = result["industry"]
        return values

    def _l10n_cz_ares_accounts_blocked(self):
        """Účty se nepřidávají bez práva je zakládat a u kontaktu vlastní firmy (ty patří do účetnictví)."""
        if not self.env["res.partner.bank"].has_access("create"):
            return True
        partner_id = self._origin.id
        return bool(partner_id) and bool(self.env["res.company"].sudo().search_count([("partner_id", "=", partner_id)]))

    def _l10n_cz_ares_new_accounts(self, result):
        """Zveřejněné účty z registru plátců DPH, které kontakt ještě nemá (v jakémkoli zápisu)."""
        if not result["payer"]:
            return []
        existing = {ares.comparable_account(bank.account_number) for bank in self.bank_ids}
        accounts = []
        for account in result["payer"]["accounts"]:
            key = ares.comparable_account(account["iban"])
            if key not in existing:
                existing.add(key)
                accounts.append(account)
        return accounts

    def _l10n_cz_ares_bank_values(self, account):
        bank_name, bic = ares.czech_bank(account["iban"])
        note = f"Zveřejněný účet v registru plátců DPH od {_format_date(account['published_on'])}"
        if account["domestic"]:
            note += f" ({account['domestic']})"
        return {
            "account_number": format_account_number(self.env, account["iban"]),
            "bank_name": bank_name or False,
            "bank_bic": bic or False,
            "note": f"{note}.",
        }

    def _l10n_cz_ares_duplicates(self, ico):
        """Jiné firmy (ne kontaktní osoby) se stejným IČO, na které uživatel vidí."""
        rows = self.env.execute_query(SQL(
            "SELECT id FROM res_partner WHERE additional_identifiers->>%s = %s AND parent_id IS NULL AND id != %s LIMIT 5",
            ICO_KEY, ico, self._origin.id or 0,
        ))
        return self.search([("id", "in", [row[0] for row in rows])])

    def _l10n_cz_ares_warnings(self, result):
        """Upozornění, která má uživatel vidět (zánik, nespolehlivý plátce, duplicity…)."""
        subject, payer = result["subject"], result["payer"]
        warnings = []
        if subject["ceased_on"]:
            warnings.append(f"Subjekt podle ARES zanikl {_format_date(subject['ceased_on'])}.")
        if payer and payer["status"] == "ANO":
            warnings.append("Pozor: v registru plátců DPH je veden jako NESPOLEHLIVÝ PLÁTCE.")
        if result["payer_error"]:
            warnings.append(f"Bankovní účty nenačteny: {result['payer_error']}")
        if not subject["address"]["standardized"]:
            warnings.append("Adresa v ARES není rozepsaná na ulici a obec, je celá v poli Ulice. Zkontroluj ji.")
        if not subject["vat"] and self.vat:
            warnings.append(f"Podle ARES není plátcem DPH, kontakt má ale DIČ {self.vat}. DIČ zůstalo, zkontroluj ho.")
        if self._l10n_cz_ares_accounts_blocked() and payer and payer["accounts"]:
            warnings.append("Bankovní účty se nepřidaly: chybí právo je zakládat, nebo jde o kontakt vlastní firmy.")
        duplicates = self._l10n_cz_ares_duplicates(result["ico"])
        if duplicates:
            warnings.append(f"Stejné IČO už má: {', '.join(duplicates.mapped('display_name'))}.")
        return warnings

    def _l10n_cz_ares_summary(self, result, added=0, to_pick=0, changes=None):
        """Krátké shrnutí pro uživatele; `changes` None znamená doplnění nového kontaktu ve formuláři."""
        subject, payer = result["subject"], result["payer"]
        if changes is None:
            text = f"Doplněno z ARES: {subject['name']}"
        elif changes:
            text = f"Aktualizováno z ARES: {', '.join(FIELD_LABELS.get(c[0], 'IČO') for c in changes)}"
        else:
            text = "Údaje odpovídají ARES"
        if subject["vat_group"]:
            text += f"; člen skupiny DPH, DIČ skupiny {subject['vat']}"
        elif not subject["vat"]:
            text += "; není plátce DPH, zveřejněné účty nemá"
        elif payer:
            text += f"; {PAYER_STATUS.get(payer['status'], payer['status'])}"
        if added and changes is None:
            text += "; po uložení přibude zveřejněný bankovní účet"
        elif added:
            text += "; přidaný zveřejněný bankovní účet"
        if to_pick and changes is None:
            text += (f".\n\nZveřejněných účtů v registru plátců DPH: {to_pick}. Po uložení klikni na "
                     "Aktualizovat z ARES a vyber, které přidat")
        elif to_pick:
            text += f"; zveřejněných účtů k výběru: {to_pick}"
        return f"{text}."

    def _l10n_cz_ares_post_message(self, result, changes, banks, warnings, to_pick=0, in_dialog=True):
        """Záznam do chatteru: co se změnilo (staré → nové), přidané účty a upozornění."""
        def show(value):
            if isinstance(value, models.BaseModel):
                return value.display_name or "–"
            return value or "–"

        body = Markup("<p>Aktualizace z ARES (IČO %s).</p>") % result["ico"]
        if changes:
            items = Markup().join(
                Markup("<li>%s: %s → %s</li>") % (FIELD_LABELS.get(name, "IČO"), show(old), show(new))
                for name, old, new in changes
            )
            body += Markup("<ul>%s</ul>") % items
        else:
            body += Markup("<p>Údaje odpovídají ARES.</p>")
        if banks:
            items = Markup().join(
                Markup("<li>%s%s</li>") % (bank.account_number, f" – {bank.bank_name}" if bank.bank_name else "")
                for bank in banks
            )
            body += Markup("<p>Přidané účty zveřejněné v registru plátců DPH:</p><ul>%s</ul>") % items
        if to_pick:
            where = "výběr v okně" if in_dialog else "vyber je tlačítkem Aktualizovat z ARES"
            body += Markup("<p>Zveřejněných účtů, které kontakt nemá: %s (%s).</p>") % (to_pick, where)
        payer = result["payer"]
        if payer:
            body += Markup("<p>Registr plátců DPH: %s.</p>") % PAYER_STATUS.get(payer["status"], payer["status"])
        for warning in warnings:
            body += Markup("<p><strong>%s</strong></p>") % warning
        self.message_post(body=body)
