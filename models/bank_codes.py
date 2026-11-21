import json
import logging

from odoo import api, fields, models

from ..tools import ares

_logger = logging.getLogger(__name__)

PARAM_BANKS = "l10n_cz_ares.cnb_bank_codes"
PARAM_UPDATED = "l10n_cz_ares.cnb_bank_codes_updated"


class AresBankCodes(models.AbstractModel):
    """Kódy bank ČNB pro název a BIC banky u účtů z registru plátců DPH.

    Cron je jednou za měsíc stáhne od ČNB a uloží do systémových parametrů. Dokud se to nepovede,
    platí seznam přibalený v modulu. Názvy bank u už založených účtů se nemění.
    """

    _name = "l10n_cz_ares.bank.codes"
    _description = "Kódy bank ČNB"

    @api.model
    def _get_banks(self) -> dict:
        """Seznam kódů bank {kód: (název, BIC)}: stažený od ČNB, jinak přibalený v modulu."""
        stored = self.env["ir.config_parameter"].sudo().get_str(PARAM_BANKS)
        if stored:
            try:
                return {code: (name, bic) for code, (name, bic) in json.loads(stored).items()}
            except (ValueError, TypeError):
                _logger.warning("Uložený seznam kódů bank je poškozený, používám přibalený.")
        return ares.bundled_czech_banks()

    @api.model
    def _cron_update(self):
        """Stáhne aktuální seznam od ČNB; když se nepovede, zůstane předchozí."""
        try:
            banks = ares.fetch_czech_banks()
        except ares.RegistryError as error:
            _logger.warning("Kódy bank od ČNB nestaženy: %s", error)
            return False
        params = self.env["ir.config_parameter"].sudo()
        params.set_str(PARAM_BANKS, json.dumps(banks, ensure_ascii=False, sort_keys=True))
        params.set_str(PARAM_UPDATED, fields.Datetime.to_string(fields.Datetime.now()))
        _logger.info("Kódy bank od ČNB aktualizované (%s bank).", len(banks))
        return True
