#!/usr/bin/env python3
"""
SANFOG — zostaví adhoc.json pre ranný standup z 00_TODO_ZAPISNIK.

Zdroj pravdy pre úlohy je 00_TODO_ZAPISNIK (jedna úloha = jeden Google Doc).
v2 (4.10.2026): otvorenosť úlohy sa určuje PÁROVANÍM ID s HOTOVO__ podľa 00_PROTOKOL_KB 8b/8j.1
(pôvodný súbor úlohy sa pri zavretí nepremenúva — zavretie je nový súbor HOTOVO__<ID>__datum__kto).
v3 (14.10.2026, vlna C, problém P4 — 16 z 59 Docov sa nenaparsovalo):
  - kľúče s diakritikou aj bez nej: ULOHA/ÚLOHA, PRECO/PREČO, TERMIN/TERMÍN, ZAPISAL/ZAPÍSAL,
    POZN/POZNÁMKA/POZNÁMKY, KTO, STAV, STANDUP, VZNIKLO V, KB (výstup používa ASCII kanonické mená),
  - kľúče v strede riadku a pipe formát: "T-… | PRIO A | KTO: Attila | TERMÍN: 20.10. ÚLOHA: …",
    aj tabuľkový tvar "| KTO | Attila |",
  - markdown: "## ULOHA" / "## Úloha" (hodnota na ďalších riadkoch), "**KTO:** x", "- KTO: x",
  - ZAPISAL je samostatné pole (v2 ho lepil do predchádzajúceho poľa, napr. do POZN),
  - HOTOVO__zlucene_do_T-<nove>__T-<stare>__… zatvára <stare> (pasca z protokolu KB, v2 ju nepoznala),
  - --report: kontrolný výpis položiek na ručnú opravu (nazov začína "T-" / > 130 znakov,
    kto = "neurčené" alebo obsahuje "TERMIN:"/"STAV:"). Výstup adhoc.json sa tým nemení.
  Pre Doc vo formáte, ktorý v2 čítal správne, je výstup v3 identický s v2.

Do standupu ide úloha, ktorej názov začína "T-", končí na "__STANDUP" a jej ID (časť pred prvým "__")
NIE JE v množine zavretých ID (z titulov HOTOVO__<ID>__… v zápisníku aj v _ARCHIV_ZAPISNIK).
adhoc.json je odvodený súbor — ručne sa nepíše (rozhodnutie A. Halász 03.10.2026, KB-2026-09-19-1338).

Vstupy:
  --titles  textový súbor: VŠETKY tituly z 00_TODO_ZAPISNIK aj _ARCHIV_ZAPISNIK, jeden na riadok
            (dopaginované, na deduplikácii nezáleží)
  --src     priečinok s textami úloh __STANDUP, názov súboru = názov Docu + ".txt"
            (download_file_content(fileId, exportMimeType="text/plain"))
  --report  (voliteľné) cesta k JSON súboru s položkami na ručnú kontrolu
Výstup: adhoc.json vo formáte generátora [{"nazov","kto","termin","stav","pozn"}].

Použitie:
    python3 zapisnik_to_adhoc_v3.py --titles /tmp/sanfog/tituly.txt --src /tmp/sanfog/zapisnik --out /tmp/sanfog/adhoc.json
"""
import argparse, json, os, re, sys, unicodedata

KEYS = ["ULOHA", "PRECO", "KTO", "TERMIN", "STAV", "STANDUP", "VZNIKLO V", "KB", "POZN", "ZAPISAL"]

# varianty kľúčov (po odstránení diakritiky a prevode na veľké písmená) -> kanonický kľúč
_ALIASES = {
    "ULOHA": "ULOHA", "PRECO": "PRECO", "KTO": "KTO", "TERMIN": "TERMIN", "STAV": "STAV",
    "STANDUP": "STANDUP", "VZNIKLO V": "VZNIKLO V", "KB": "KB", "POZN": "POZN",
    "POZNAMKA": "POZN", "POZNAMKY": "POZN", "ZAPISAL": "ZAPISAL",
    # kľúče, ktoré sa do adhoc.json nedávajú, ale UKONČUJÚ predchádzajúce pole
    # (inak by sa "ZDROJ: …" alebo "DENNIK: D-…" prilepili k ÚLOHE / TERMÍNU)
    "ID": "_IGN", "PRIO": "_IGN", "PRIORITA": "_IGN", "PODKLAD": "_IGN", "PODKLADY": "_IGN",
    "DENNIK": "_IGN", "ZDROJ": "_IGN", "DOPLNIT": "_IGN", "SUVISIACE": "_IGN", "PROJEKT": "_IGN",
}
# alternácia pre regex (dlhšie skôr, nech POZNÁMKA nevyhrá ako POZN)
_KEY_ALT = (r"ÚLOHA|ULOHA|PREČO|PRECO|KTO|TERMÍN|TERMIN|STAV|STANDUP|VZNIKLO V|KB|"
            r"POZNÁMKY|POZNAMKY|POZNÁMKA|POZNAMKA|POZN\.|POZN|ZAPÍSAL|ZAPISAL|"
            r"ID|PRIORITA|PRIO|PODKLADY|PODKLAD|DENNÍK|DENNIK|ZDROJ|DOPLNIŤ|DOPLNIT|SÚVISIACE|SUVISIACE|PROJEKT")

# kľúč na začiatku riadku — VEĽKÉ písmená (ako v2, rozšírené o diakritiku)
KEY_RE = re.compile(r"^(" + _KEY_ALT + r")\s*:\s*(.*)$")
# kľúč na začiatku riadku v tvare "Úloha:" / "Kto:" (len prvé písmeno veľké) — akceptuje sa,
# iba ak dané pole ešte nie je vyplnené (aby veta "Stav: …" v POZN neprepísala STAV)
KEY_TITLE_RE = re.compile(r"^(" + _KEY_ALT + r")\s*:\s*(.*)$", re.IGNORECASE)
# kľúč v strede riadku: VEĽKÉ písmená, pred ním začiatok/medzera/|, za ním dvojbodka
MID_RE = re.compile(r"(?:(?<=^)|(?<=[\s|]))(" + _KEY_ALT + r")\s*:")
# markdown nadpis "## ULOHA" / "## Úloha:" (+ voliteľne hodnota na tom istom riadku)
HEAD_RE = re.compile(r"^#{1,6}\s*(" + _KEY_ALT + r")\s*(?::\s*(.*))?$", re.IGNORECASE)
# tabuľkový tvar "| KTO | Attila |" -> bunka je presne názov kľúča (VEĽKÉ písmená)
CELL_KEY_RE = re.compile(r"^(" + _KEY_ALT + r")$")


def _canon(k):
    k = unicodedata.normalize("NFKD", k)
    k = "".join(c for c in k if not unicodedata.combining(c)).upper()
    k = re.sub(r"\s+", " ", k).strip().rstrip(".")
    return _ALIASES.get(k)


def _clean_line(line):
    """Odstráni markdown ozdoby, ktoré bránia rozpoznaniu kľúča (len pre detekciu kľúča)."""
    s = line.strip()
    s = s.replace("**", "")
    s = re.sub(r"^(?:[-*•>]\s+)+", "", s)          # odrážky / citácie
    return s.strip()


def _split_mid(s):
    """Rozdelí text s kľúčmi v strede na [(kľúč, hodnota)]. Text pred prvým kľúčom vráti ako (None, text)."""
    out = []
    ms = list(MID_RE.finditer(s))
    if not ms:
        return [(None, s)]
    if ms[0].start() > 0:
        out.append((None, s[:ms[0].start()]))
    for i, m in enumerate(ms):
        end = ms[i + 1].start() if i + 1 < len(ms) else len(s)
        out.append((_canon(m.group(1)), s[m.end():end]))
    return out


def _parse_line(line, fields, cur=None):
    """Vráti zoznam (kľúč, hodnota) nájdených v riadku, alebo None, ak riadok nie je kľúčový."""
    s = _clean_line(line)
    if not s:
        return None
    # 1) markdown nadpis s kľúčom
    m = HEAD_RE.match(s)
    if m:
        return [(_canon(m.group(1)), (m.group(2) or "").strip())]
    # 2) pipe formát / markdown tabuľka
    if "|" in s:
        cells = [c.strip() for c in s.strip("|").split("|")]
        pairs, i, found = [], 0, False
        while i < len(cells):
            c = cells[i]
            if CELL_KEY_RE.match(c) and i + 1 < len(cells) and not MID_RE.match(cells[i + 1]):
                pairs.append((_canon(c), cells[i + 1]))
                found = True
                i += 2
                continue
            for k, v in _split_mid(c):
                if k:
                    pairs.append((k, v.strip()))
                    found = True
            i += 1
        if found:
            return [(k, v.strip()) for k, v in pairs]
    # 3) kľúč na začiatku riadku (VEĽKÉ písmená), prípadne ďalšie kľúče v strede
    m = KEY_RE.match(s)
    if m:
        parts = _split_mid(s)
        return [(k, v.strip()) for k, v in parts if k]
    # 4) "Úloha:" / "Kto:" — len ak pole ešte nie je vyplnené
    m = KEY_TITLE_RE.match(s)
    if m and s[0].isupper() and cur != "POZN":
        k = _canon(m.group(1))
        if k and k not in fields:
            return [(k, m.group(2).strip())]
    return None


def parse(text):
    text = text.replace("﻿", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\_", "_")          # pre istotu, keby šlo o markdown export
    fields, cur = {}, None
    for line in text.split("\n"):
        pairs = _parse_line(line, fields, cur)
        if not pairs and re.match(r"^\s*#{1,6}\s", line) and cur != "POZN":
            cur = None                        # iný markdown nadpis ukončí pole (okrem POZN)
            continue
        if pairs:
            for k, v in pairs:
                if k == "POZN" and "POZN" in fields:
                    # druhý výskyt POZN (ako v2): pokračovanie poznámky, nie nové pole
                    fields["POZN"] += "\n" + line.rstrip()
                    cur = "POZN"
                    break
                cur = k
                fields[cur] = v
            continue
        if cur == "POZN":                     # POZN je posledné a môže mať viac odsekov
            fields["POZN"] += "\n" + line.rstrip()
        elif cur and line.strip():
            fields[cur] = (fields[cur] + " " + line.strip()) if fields[cur] else line.strip()
    fields.pop("_IGN", None)
    for k in fields:
        fields[k] = re.sub(r"\n{3,}", "\n\n", fields[k]).strip()
    return fields


def closed_ids(titles):
    closed = set()
    for t in titles:
        t = t.strip().lstrip("﻿")
        if not t.startswith("HOTOVO__"):
            continue
        segs = t[len("HOTOVO__"):].split("__")
        if segs and segs[0].startswith("zlucene_do_") and len(segs) > 1:
            closed.add(segs[1])               # HOTOVO__zlucene_do_T-X__T-Y__… zatvára Y, nie X
        else:
            closed.add(segs[0])
    return closed


def needs_review(item, title):
    why = []
    if item["nazov"].startswith("T-"):
        why.append("nazov = strojový názov Docu")
    if len(item["nazov"]) > 130:
        why.append("nazov > 130 znakov")
    if item["kto"] == "neurčené":
        why.append("kto neurčené")
    if re.search(r"TERM[IÍ]N\s*:|STAV\s*:", item["kto"]):
        why.append("kto obsahuje TERMIN:/STAV:")
    return why


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--titles", required=True)
    ap.add_argument("--report", default=None)
    a = ap.parse_args()

    closed = closed_ids(open(a.titles, encoding="utf-8").read().splitlines())

    items, skipped, review = [], [], []
    for fn in sorted(os.listdir(a.src)):
        if not fn.endswith(".txt"):
            continue
        title = fn[:-4]
        if not title.startswith("T-") or not title.endswith("__STANDUP"):
            skipped.append(title)
            continue
        if title.split("__")[0] in closed:
            skipped.append(title)
            continue
        f = parse(open(os.path.join(a.src, fn), encoding="utf-8").read())
        stav = f.get("STAV", "Otvorené") or "Otvorené"
        pozn = f.get("POZN", "")
        if f.get("PRECO") and f["PRECO"] not in ("—", "-"):
            pozn = (f["PRECO"] + ("\n\n" + pozn if pozn else "")).strip()
        item = {
            "nazov": f.get("ULOHA") or title,
            "kto": f.get("KTO", "neurčené") or "neurčené",
            "termin": f.get("TERMIN", "—") or "—",
            "stav": stav,
            "pozn": pozn,
        }
        items.append(item)
        why = needs_review(item, title)
        if why:
            review.append({"titul": title, "dovod": why})
    s = json.dumps(items, ensure_ascii=False, indent=2)
    json.loads(s)
    open(a.out, "w", encoding="utf-8").write(s)
    print(f"adhoc.json: {len(items)} otvorených úloh __STANDUP (preskočené: {len(skipped)}, zavretých ID: {len(closed)})")
    print(f"NA_RUCNU_KONTROLU={len(review)}")
    for r in review:
        print(f"  KONTROLA: {r['titul']} — {', '.join(r['dovod'])}", file=sys.stderr)
    if a.report:
        open(a.report, "w", encoding="utf-8").write(json.dumps(review, ensure_ascii=False, indent=2))
    if not items:
        print("POZOR: žiadna úloha so __STANDUP — sekcia MIMO SYSTÉMU bude prázdna", file=sys.stderr)


if __name__ == "__main__":
    main()
