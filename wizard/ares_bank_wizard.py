from odoo import fields, models


class AresBankWizard(models.TransientModel):
    """Výběr zveřejněných účtů, když jich kontakt v registru plátců DPH má víc, než má v Odoo."""

    _name = "l10n_cz_ares.bank.wizard"
    _description = "Výběr účtů z registru plátců DPH"

    partner_id = fields.Many2one("res.partner", string="Kontakt", required=True, readonly=True, ondelete="cascade")
    summary = fields.Text(string="Výsledek aktualizace", readonly=True)
    line_ids = fields.One2many("l10n_cz_ares.bank.wizard.line", "wizard_id", string="Účty")

    def action_import(self):
        """Přidá vybrané účty."""
        return self._import(self.line_ids.filtered("selected"))

    def action_import_all(self):
        """Přidá všechny nabídnuté účty."""
        return self._import(self.line_ids)

    def _import(self, lines):
        self.ensure_one()
        banks = self.partner_id._l10n_cz_ares_create_banks([line._bank_values() for line in lines])
        if banks:
            self.partner_id._l10n_cz_ares_post_banks(banks)
        count = len(banks)
        message = {0: "Žádný účet nepřidán.", 1: "Přidán 1 bankovní účet."}.get(count) or (
            f"Přidány {count} bankovní účty." if count < 5 else f"Přidáno {count} bankovních účtů."
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "ARES",
                "message": message,
                "type": "success" if count else "info",
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class AresBankWizardLine(models.TransientModel):
    _name = "l10n_cz_ares.bank.wizard.line"
    _description = "Účet z registru plátců DPH"
    _order = "published_on desc, id"

    wizard_id = fields.Many2one("l10n_cz_ares.bank.wizard", required=True, ondelete="cascade")
    selected = fields.Boolean(string="Přidat")
    display_number = fields.Char(string="Číslo účtu", readonly=True, help="Tuzemský tvar u českých účtů, jinak IBAN.")
    account_number = fields.Char(string="IBAN", required=True, readonly=True)
    bank_name = fields.Char(string="Banka", readonly=True)
    bank_bic = fields.Char(string="BIC", readonly=True)
    published_on = fields.Date(string="Zveřejněno", readonly=True)
    description = fields.Char(string="Popis", readonly=True)
    note = fields.Char(readonly=True)

    def _bank_values(self):
        self.ensure_one()
        return {
            "account_number": self.account_number,
            "bank_name": self.bank_name,
            "bank_bic": self.bank_bic,
            "note": self.note,
        }
