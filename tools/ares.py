"""Dotazy do ARES a registru plátců DPH a převod odpovědí na údaje kontaktu.

Funkce nepracují s databází, jen s odpověďmi registrů, aby šly testovat s podstrčenými daty.
Texty výjimek jsou pro uživatele (česky).
"""

import csv
import functools
import re

import requests
from lxml import etree

from odoo.tools import file_open

ARES_URL = "https://ares.gov.cz/ekonomicke-subjekty-v-be/rest"
VAT_REGISTRY_URL = "https://adisrws.mfcr.cz/dpr/axis2/services/rozhraniCRPDPH.rozhraniCRPDPHSOAP"
VAT_REGISTRY_NS = "http://adis.mfcr.cz/rozhraniCRPDPH/"
CNB_BANK_CODES_URL = "https://www.cnb.cz/cs/platebni-styk/.galleries/ucty_kody_bank/download/kody_bank_CR.csv"
MIN_BANK_CODES = 20  # v listopadu 2026 jich ČNB vede 47; méně znamená rozbitý soubor
TIMEOUT = 10
USER_AGENT = "Odoo l10n_cz_ares"
EVIDENCE_NUMBER_TYPE = 2  # typCisloDomovni: 1 = číslo popisné, 2 = číslo evidenční
ACTIVE = "AKTIVNI"

# Sekce CZ-NACE (= NACE Rev. 2) podle oddílu, tedy prvních dvou číslic kódu. Odpovídají oborům
# `base.res_partner_industry_A` až `_U` v Odoo.
NACE_SECTIONS = (
    (1, 3, "A"), (5, 9, "B"), (10, 33, "C"), (35, 35, "D"), (36, 39, "E"), (41, 43, "F"),
    (45, 47, "G"), (49, 53, "H"), (55, 56, "I"), (58, 63, "J"), (64, 66, "K"), (68, 68, "L"),
    (69, 75, "M"), (77, 82, "N"), (84, 84, "O"), (85, 85, "P"), (86, 88, "Q"), (90, 93, "R"),
    (94, 96, "S"), (97, 98, "T"), (99, 99, "U"),
)
DOMESTIC_ACCOUNT = re.compile(r"(?:(\d{1,6})-)?(\d{2,10})/(\d{4})")


class RegistryError(Exception):
    """Registr neodpověděl nebo odpověděl chybou."""


class SubjectNotFound(RegistryError):
    """IČO v ARES není."""


# ---------------------------------------------------------------- IČO a DIČ


def is_valid_ico(ico: str) -> bool:
    """Kontrolní číslice IČO (modulo 11, váhy 8 až 2)."""
    if not re.fullmatch(r"\d{8}", ico or ""):
        return False
    total = sum(int(digit) * weight for digit, weight in zip(ico[:7], range(8, 1, -1)))
    return int(ico[7]) == (11 - total % 11) % 10


def normalize_ico(value: str) -> str:
    """Osmimístné IČO ze vstupu uživatele; přijme mezery i DIČ právnické osoby (CZ + IČO).

    :raises ValueError: s textem pro uživatele, když vstup IČO není
    """
    digits = re.sub(r"\s", "", value or "").upper().removeprefix("CZ")
    if not re.fullmatch(r"\d{1,8}", digits):
        raise ValueError(f"„{value}“ není IČO. IČO má nejvýš 8 číslic (DIČ z rodného čísla na IČO převést nejde).")
    ico = digits.zfill(8)
    if not is_valid_ico(ico):
        raise ValueError(f"IČO {ico} nemá správnou kontrolní číslici, zkontroluj ho.")
    return ico


def ico_from_vat(vat: str | None) -> str | None:
    """IČO z českého DIČ právnické osoby (CZ + 8 číslic), jinak None."""
    compact = re.sub(r"\W", "", vat or "").upper()
    if re.fullmatch(r"CZ\d{8}", compact) and is_valid_ico(compact[2:]):
        return compact[2:]
    return None


def vat_digits(vat: str | None) -> str | None:
    """Číselná část českého DIČ pro dotaz do registru plátců DPH (bez předpony CZ)."""
    compact = re.sub(r"\W", "", vat or "").upper()
    match = re.fullmatch(r"(?:CZ)?(\d{8,10})", compact)
    return match.group(1) if match else None


# ---------------------------------------------------------------- HTTP


def _request(method: str, url: str, **kwargs) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    try:
        return requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as error:
        raise RegistryError(f"Server {url.split('/')[2]} neodpovídá ({error.__class__.__name__}). Zkus to za chvíli.") from error


def _json(response: requests.Response, registry: str) -> dict:
    try:
        return response.json()
    except ValueError as error:
        raise RegistryError(f"{registry} vrátil nečitelnou odpověď.") from error


def fetch_subject(ico: str) -> dict:
    """Záznam ekonomického subjektu z ARES (JSON odpovědi jako dict)."""
    response = _request("GET", f"{ARES_URL}/ekonomicke-subjekty/{ico}")
    if response.status_code == 404:
        raise SubjectNotFound(f"IČO {ico} v ARES není.")
    if response.status_code != 200:
        detail = ""
        try:
            detail = response.json().get("popis") or ""
        except ValueError:
            pass
        raise RegistryError(f"ARES vrátil chybu {response.status_code}. {detail}".strip())
    return _json(response, "ARES")


def fetch_prevailing_nace(ico: str) -> str | None:
    """Převažující činnost (CZ-NACE 2008) z Registru ekonomických subjektů, když ji ARES zná."""
    response = _request("GET", f"{ARES_URL}/ekonomicke-subjekty-res/{ico}")
    if response.status_code != 200:
        return None
    records = _json(response, "ARES").get("zaznamy") or []
    record = next((r for r in records if r.get("primarniZaznam")), records[0] if records else {})
    return record.get("czNacePrevazujici2008") or None


def fetch_vat_payer(dic: str) -> dict:
    """Stav plátce DPH a zveřejněné bankovní účty z registru plátců DPH (MF, SOAP).

    :param dic: číselná část DIČ bez CZ
    :return: {"status": "NE" | "ANO" | "NENALEZEN", "accounts": [{"iban", "domestic", "published_on"}]}
    """
    if not re.fullmatch(r"\d{8,10}", dic):
        raise RegistryError(f"DIČ {dic} nemá tvar českého DIČ.")
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:roz="{VAT_REGISTRY_NS}">'
        "<soapenv:Header/><soapenv:Body><roz:StatusNespolehlivyPlatceRozsirenyRequest>"
        f"<roz:dic>{dic}</roz:dic>"
        "</roz:StatusNespolehlivyPlatceRozsirenyRequest></soapenv:Body></soapenv:Envelope>"
    )
    response = _request(
        "POST",
        VAT_REGISTRY_URL,
        data=envelope.encode(),
        headers={
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f"{VAT_REGISTRY_NS}getStatusNespolehlivyPlatceRozsireny",
        },
    )
    if response.status_code != 200:
        raise RegistryError(f"Registr plátců DPH vrátil chybu {response.status_code}.")
    return parse_vat_payer(response.content, dic)


# ---------------------------------------------------------------- převod odpovědí


def parse_vat_payer(content: bytes, dic: str) -> dict:
    """Rozbor odpovědi getStatusNespolehlivyPlatceRozsireny pro jedno DIČ."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.fromstring(content, parser)
    except etree.XMLSyntaxError as error:
        raise RegistryError("Registr plátců DPH vrátil nečitelnou odpověď.") from error
    ns = {"r": VAT_REGISTRY_NS}
    status = root.find(".//r:status", ns)
    if status is None or status.get("statusCode") != "0":
        text = status.get("statusText") if status is not None else "odpověď bez stavu"
        raise RegistryError(f"Registr plátců DPH vrátil chybu: {text}")
    payer = next((p for p in root.iterfind(".//r:statusPlatceDPH", ns) if p.get("dic") == dic), None)
    if payer is None:
        return {"status": "NENALEZEN", "accounts": []}
    accounts = []
    for account in payer.iterfind("r:zverejneneUcty/r:ucet", ns):
        published_on = account.get("datumZverejneni")
        standard = account.find("r:standardniUcet", ns)
        other = account.find("r:nestandardniUcet", ns)
        if standard is not None and standard.get("cislo") and standard.get("kodBanky"):
            prefix, number, bank_code = standard.get("predcisli"), standard.get("cislo"), standard.get("kodBanky")
            accounts.append({
                "iban": czech_account_to_iban(prefix, number, bank_code),
                "domestic": f"{prefix}-{number}/{bank_code}" if prefix else f"{number}/{bank_code}",
                "published_on": published_on,
            })
        elif other is not None and other.get("cislo"):
            iban = re.sub(r"\s", "", other.get("cislo")).upper()
            accounts.append({"iban": iban, "domestic": iban_to_domestic(iban), "published_on": published_on})
    return {"status": payer.get("nespolehlivyPlatce"), "accounts": accounts}


def house_number(seat: dict) -> str:
    """Číslo domu jako v ARES: popisné/orientační s písmenem („1444/2“, „525/15a“)."""
    number = str(seat.get("cisloDomovni") or "")
    orientation = f"{seat.get('cisloOrientacni') or ''}{seat.get('cisloOrientacniPismeno') or ''}"
    if number and orientation:
        return f"{number}/{orientation}"
    return number or orientation


def format_zip(psc) -> str:
    """PSČ ve tvaru „140 00“."""
    digits = re.sub(r"\D", "", str(psc or ""))
    return f"{digits[:3]} {digits[3:]}" if len(digits) == 5 else digits


def parse_address(seat: dict) -> dict:
    """Sídlo z ARES jako adresa kontaktu, ve stejném tvaru jako `textovaAdresa` v ARES.

    S ulicí: „Duhová 1444/2“, část obce (Michle) do druhého řádku. Bez ulice: „Třtěno 114“, když
    se část obce jmenuje jinak než obec, jinak „č.p. 337“; u čísla evidenčního „č.ev.“.
    V Praze je město obvod („Praha 4“). Nestandardizovanou adresu (jen text) dá celou do ulice.
    """
    municipality = seat.get("nazevObce") or ""
    part = seat.get("nazevCastiObce") or ""
    street_name = seat.get("nazevUlice") or ""
    city = seat.get("nazevMestskehoObvodu") if municipality == "Praha" and seat.get("nazevMestskehoObvodu") else municipality
    address = {
        "country_code": seat.get("kodStatu") or "CZ",
        "street": "",
        "street2": "",
        "city": city,
        "zip": format_zip(seat.get("psc")),
        "standardized": True,
    }
    number = house_number(seat)
    if not municipality or not (number or street_name):
        address["street"] = re.sub(r",?\s*Česká republika\s*$", "", seat.get("textovaAdresa") or "").strip()
        address["standardized"] = False
        return address
    evidence = "č.ev. " if seat.get("typCisloDomovni") == EVIDENCE_NUMBER_TYPE else ""
    if street_name:
        address["street"] = f"{street_name} {evidence}{number}".strip()
        if part and part != municipality:
            address["street2"] = part
    elif part and part != municipality:
        address["street"] = f"{part} {evidence}{number}"
    else:
        address["street"] = f"{evidence or 'č.p. '}{number}"
    return address


def vat_from_subject(data: dict) -> tuple[str | None, bool]:
    """DIČ platné pro DPH a příznak skupiny.

    Člen skupiny DPH má v ARES své DIČ s ukončenou registrací, pro DPH platí DIČ skupiny
    (`dicSkDph`). Kdo plátcem ani identifikovanou osobou není, DIČ pro DPH nemá.
    """
    registrations = data.get("seznamRegistraci") or {}
    if registrations.get("stavZdrojeSkDph") == ACTIVE and data.get("dicSkDph"):
        return data["dicSkDph"], True
    if registrations.get("stavZdrojeDph") == ACTIVE and data.get("dic"):
        return data["dic"], False
    return None, False


def parse_subject(data: dict) -> dict:
    """Údaje kontaktu ze záznamu ARES."""
    vat, vat_group = vat_from_subject(data)
    return {
        "ico": data.get("ico"),
        "name": (data.get("obchodniJmeno") or "").strip(),
        "address": parse_address(data.get("sidlo") or {}),
        "vat": vat,
        "vat_group": vat_group,
        "ceased_on": data.get("datumZaniku"),
    }


def nace_section(code: str | None) -> str | None:
    """Sekce NACE (písmeno A až U) z kódu CZ-NACE 2008, None pro neznámý kód („00“)."""
    if not code or not code[:2].isdigit():
        return None
    division = int(code[:2])
    return next((section for low, high, section in NACE_SECTIONS if low <= division <= high), None)


# ---------------------------------------------------------------- bankovní účty


def czech_account_to_iban(prefix: str | None, number: str, bank_code: str) -> str:
    """IBAN z čísla účtu ve tvaru předčíslí-číslo/kód banky."""
    bban = f"{bank_code:0>4}{prefix or '':0>6}{number:0>10}"
    check = 98 - int(f"{bban}123500") % 97  # „CZ00“ přesunuté na konec, C = 12, Z = 35
    return f"CZ{check:02d}{bban}"


def iban_to_domestic(iban: str) -> str | None:
    """Tuzemský tvar (předčíslí-číslo/kód banky) českého IBAN, u jiného None.

    Registr plátců DPH dává některé české účty jen jako IBAN („nestandardní“), lidé je ale znají
    a na fakturách vidí v tuzemském tvaru.
    """
    if not re.fullmatch(r"CZ\d{22}", iban or ""):
        return None
    bank_code, prefix, number = iban[4:8], iban[8:14].lstrip("0"), iban[14:].lstrip("0").zfill(2)
    return f"{prefix}-{number}/{bank_code}" if prefix else f"{number}/{bank_code}"


def comparable_account(account_number: str | None) -> str:
    """Číslo účtu pro porovnání: tuzemský tvar převedený na IBAN, jinak bez mezer velkými."""
    text = (account_number or "").strip()
    match = DOMESTIC_ACCOUNT.fullmatch(re.sub(r"\s", "", text))
    if match:
        return czech_account_to_iban(*match.groups())
    return re.sub(r"[\W_]", "", text).upper()


@functools.cache
def bundled_czech_banks() -> dict:
    """Kódy bank ČNB přibalené v modulu (data/cnb_bank_codes.csv): {kód: (název, BIC)}."""
    with file_open("l10n_cz_ares/data/cnb_bank_codes.csv") as file:
        return {row["code"]: (row["name"], row["bic"] or None) for row in csv.DictReader(file, delimiter=";")}


def parse_bank_codes(text: str) -> dict:
    """Seznam kódů bank z CSV ČNB (kód;název;BIC;CERTIS, první řádek hlavička): {kód: (název, BIC)}.

    :raises RegistryError: když soubor nevypadá jako seznam ČNB (změna formátu, chybová stránka)
    """
    rows = list(csv.reader(text.lstrip("﻿").splitlines(), delimiter=";"))
    banks = {
        row[0].strip(): (row[1].strip(), row[2].strip() or None)
        for row in rows[1:]
        if len(row) >= 3 and re.fullmatch(r"\d{4}", row[0].strip()) and row[1].strip()
    }
    if not rows or "kód" not in rows[0][0].lower() or len(banks) < MIN_BANK_CODES:
        raise RegistryError("Seznam kódů bank od ČNB má nečekaný tvar, zůstává předchozí.")
    return banks


def fetch_czech_banks() -> dict:
    """Aktuální seznam kódů bank od ČNB: {kód: (název, BIC)}."""
    response = _request("GET", CNB_BANK_CODES_URL)
    if response.status_code != 200:
        raise RegistryError(f"ČNB vrátila chybu {response.status_code}.")
    try:
        text = response.content.decode("utf-8-sig")
    except UnicodeDecodeError:  # dřív ČNB dávala soubory ve windows-1250
        text = response.content.decode("cp1250")
    return parse_bank_codes(text)


def czech_bank(iban: str, banks: dict | None = None) -> tuple[str | None, str | None]:
    """Název a BIC banky českého IBAN podle kódu banky, jinak (None, None).

    :param banks: seznam kódů bank {kód: (název, BIC)}; bez něj ten přibalený v modulu
    """
    if not iban.startswith("CZ") or len(iban) != 24:
        return None, None
    return (banks or bundled_czech_banks()).get(iban[4:8], (None, None))
