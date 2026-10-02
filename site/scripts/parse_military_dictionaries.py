"""Extrait en JSON (terme -> definition) les deux dictionnaires militaires
telecharges le 2026-10-02 dans site/data/dictionaries/military/ (demande :
"trouve un dictionnaire militaire", sources validees par l'utilisateur) :

1. dod-jp1-02.pdf : DoD Dictionary of Military and Associated Terms, JP 1-02,
   8 novembre 2010 (amende jusqu'au 15 septembre 2011), 549 pages, document du
   gouvernement americain (domaine public), anglais. Format regulier
   "terme — definition. Also called ABC. (JP x-xx)".
2. nato-aap-06-2020.pdf : AAP-06 Edition 2020, Glossaire OTAN de termes et
   definitions (anglais et francais), 300 pages, trois colonnes. SEULE la partie
   ANGLAISE (termes anglais avec equivalent francais du terme, definition
   anglaise, source, date) est extraite ici ; la partie francaise (definitions
   en francais, dont la mise en page a des onglets lateraux qui s'entremelent
   avec les colonnes) n'est PAS extraite - limite assumee, a traiter si le
   besoin des definitions francaises se confirme. Copie hebergee par le COEMED,
   pas par l'OTAN, sans licence ouverte explicite : usage local de lecture,
   pas de redistribution (dossier ignore par git).

Usage : python scripts/parse_military_dictionaries.py
Sorties : site/data/dictionaries/military/dod-terms.json, nato-terms-en.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pdfplumber

DIR = Path(__file__).resolve().parent.parent / "data" / "dictionaries" / "military"

_ENTRY_START = re.compile(r"^(?P<term>[^\s—][^—]{0,110}?) — (?P<rest>.*)$")
_DOD_NOISE = re.compile(r"^(As Amended Through .*|JP 1-02 \d+|\d+ JP 1-02|[A-Z])$")


def parse_dod() -> list[dict]:
    lines: list[str] = []
    with pdfplumber.open(DIR / "dod-jp1-02.pdf") as pdf:
        for number, page in enumerate(pdf.pages, start=1):
            if number < 30:  # preliminaires et table des matieres
                continue
            for line in (page.extract_text() or "").splitlines():
                line = line.strip()
                if line and not _DOD_NOISE.match(line):
                    lines.append(line)

    # Terme coupe sur deux lignes ("prevention of mutual / interference (EMI) — ...") :
    # la 1re ligne n'a pas de " — ", la suivante en a un et commence par une
    # minuscule - on les recolle avant de decouper les entrees (constate sur
    # l'echantillon : "prevention" restait colle a la fin de l'entree
    # precedente).
    merged: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        nxt = lines[index + 1] if index + 1 < len(lines) else None
        previous_ended = bool(merged) and merged[-1].rstrip().endswith((".", ")", ";"))
        if (nxt and " — " not in line and len(line) < 80 and previous_ended and line[:1].islower()
                and _ENTRY_START.match(nxt) and nxt[:1].islower()):
            merged.append(line + " " + nxt)
            index += 2
            continue
        merged.append(line)
        index += 1

    entries: list[dict] = []
    for line in merged:
        match = _ENTRY_START.match(line)
        if match:
            entries.append({"term": match.group("term").strip(), "definition": match.group("rest").strip()})
        elif entries:
            entries[-1]["definition"] += " " + line

    for entry in entries:
        definition = entry["definition"]
        source = re.findall(r"\(((?:JP|DODD|DODI|CJCSI|FM|AR|NATO|STANAG)[^()]*)\)\s*$", definition)
        entry["doctrine_source"] = source[-1] if source else None
        acronyms = re.findall(r"Also called ([^.]+)\.", definition)
        entry["also_called"] = [a.strip() for a in re.split(r"\bor\b|,", acronyms[0])] if acronyms else []
        entry["nato_agreed"] = definition.startswith("(*)")
    return entries


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COLUMNS = ((60, 235), (235, 400), (400, 580))


def parse_nato_english() -> list[dict]:
    stream: list[str] = []
    with pdfplumber.open(DIR / "nato-aap-06-2020.pdf") as pdf:
        french_start = None
        for number, page in enumerate(pdf.pages, start=1):
            if "FRENCH TERMS AND" in (page.extract_text() or ""):
                french_start = number
                break
        for number, page in enumerate(pdf.pages, start=1):
            if number < 8 or (french_start and number >= french_start):
                continue  # preliminaires ; partie francaise non extraite
            for left, right in _COLUMNS:
                text = page.crop((left, 48, right, 795)).extract_text() or ""
                stream.extend(line.strip() for line in text.splitlines() if line.strip())

    entries: list[dict] = []
    buffer: list[str] = []
    for line in stream:
        if _DATE.match(line):
            if buffer:
                entries.append(_nato_entry(buffer, line))
            buffer = []
        else:
            buffer.append(line)
    return [e for e in entries if e]


def _nato_entry(lines: list[str], date: str) -> dict | None:
    if not lines or " / " not in lines[0]:
        return None  # suite d'une entree coupee par une page, ou bruit
    header = lines[0]
    rest = lines[1:]
    if rest and (rest[0][:1].islower() or rest[0][:1] == "'") and len(rest[0]) <= 40:
        header += " " + rest[0]  # le terme francais se poursuit sur la ligne suivante
        rest = rest[1:]
    term_en, term_fr = header.split(" / ", 1)
    text = " ".join(rest)
    source = re.findall(r"\[((?:derived from|C-M|MC|AC|PO|STANAG)[^\]]*)\]", text)
    return {
        "term_en": term_en.strip(), "term_fr": term_fr.strip(),
        "definition": re.sub(r"\s+", " ", text).strip(), "source": source[-1] if source else None, "date": date,
    }


def main() -> None:
    dod = parse_dod()
    (DIR / "dod-terms.json").write_text(json.dumps(dod, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"DoD JP 1-02 : {len(dod)} termes -> dod-terms.json "
          f"({sum(1 for e in dod if e['nato_agreed'])} marques OTAN (*), {sum(1 for e in dod if e['also_called'])} avec sigle)")
    nato = parse_nato_english()
    (DIR / "nato-terms-en.json").write_text(json.dumps(nato, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"NATO AAP-06 (partie anglaise) : {len(nato)} termes -> nato-terms-en.json")


if __name__ == "__main__":
    main()
