import os

import json

from PIL import Image

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def sanitize_folder(name: str) -> str:
    """Nettoie le nom du fournisseur pour en faire un nom de dossier valide."""
    keep = "".join(
        c if (c.isalnum() or c in "-_. ") else "_"
        for c in name
    ).strip()
    return keep or "INCONNU"


def image_to_pdf(image_path: str, pdf_path: str) -> None:
    """Convertit une image en PDF (solution de repli)."""
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    with Image.open(image_path) as img:
        img.convert("RGB").save(pdf_path, "PDF", resolution=150)


def _s(v) -> str:
    """Affiche une valeur en texte, ou tiret si vide."""
    if v is None or str(v).strip() == "":
        return "—"
    return str(v)


def _mon(v, devise: str = "DT") -> str:
    """Affiche un montant en DINAR tunisien : toujours « DT », 3 décimales
    (millimes). La devise extraite n'est pas utilisée à l'affichage : les
    factures de soins tunisiennes sont libellées en dinars."""
    if v is None or v == "":
        return "—"
    try:
        nombre = f"{float(v):,.3f}".replace(",", " ")
    except (TypeError, ValueError):
        return str(v)
    return f"{nombre} DT"


def _style():
    styles = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle(
            "Titre", parent=styles["Title"], fontSize=18,
            textColor=colors.HexColor("#1a3a6b"), spaceAfter=2,
        ),
        "sous": ParagraphStyle(
            "Sous", parent=styles["Normal"], fontSize=10,
            textColor=colors.HexColor("#666666"),
        ),
        "h": ParagraphStyle(
            "H", parent=styles["Heading2"], fontSize=13,
            textColor=colors.HexColor("#1a3a6b"),
            spaceBefore=8, spaceAfter=4,
        ),
        "cellule": ParagraphStyle(
            "Cell", parent=styles["Normal"], fontSize=9.5, leading=12,
        ),
        "label": ParagraphStyle(
            "Lbl", parent=styles["Normal"], fontSize=9, leading=12,
            textColor=colors.HexColor("#333333"),
        ),
    }
def _cell(st, texte) -> Paragraph:
    """Enveloppe une valeur dans un Paragraph : permet le retour à la ligne
    au lieu d'empiéter sur la cellule voisine dans les tableaux."""
    from xml.sax.saxutils import escape as _xml_escape
    if texte is None:
        texte = ""
    return Paragraph(_xml_escape(str(texte)) or " ", st["cellule"])


_NOMS_SECTIONS = {
    "FACTURE": "Informations facture",
    "ASSURE_PATIENT": "Assuré / Patient",
    "ASSURÉ / PATIENT": "Assuré / Patient",
    "PATIENT": "Assuré / Patient",
    "PROFESSIONNEL": "Professionnel de santé",
    "ACTES": "Actes réalisés",
    "REMBOURSEMENT": "Remboursement",
}


def _rendre_informations(infos: dict, st: dict) -> list:
    """Transforme le bloc « informations » de l'analyse anti-fraude en lignes
    lisibles « libellé : valeur » (le dict imbriqué ne doit JAMAIS apparaître
    comme code brut dans le PDF)."""
    from xml.sax.saxutils import escape as _xml_escape

    def _libelle(cle):
        return _s(cle).replace("_", " ").strip().capitalize()

    def _nice(v):
        """Convertit n'importe quelle valeur en texte lisible (jamais de repr)."""
        if v is None or str(v).strip() == "":
            return None
        if isinstance(v, (list, tuple)):
            parties = [e for e in (_nice(i) for i in v) if e]
            return " • ".join(parties) if parties else None
        if isinstance(v, dict):
            parties = []
            for k, x in v.items():
                s = _nice(x)
                if s:
                    parties.append(f"{_libelle(k)} : {s}")
            return " ; ".join(parties) if parties else None
        # Certaines réponses modèle / anciennes données stockent un dict ou
        # une liste sérialisés en texte (JSON ou repr Python) : on les re-parse.
        texte = str(v).strip()
        if texte[:1] in ("{", "[") and texte[-1:] in ("}", "]"):
            milieu = None
            try:
                milieu = json.loads(texte)
            except (ValueError, TypeError):
                try:
                    import ast
                    milieu = ast.literal_eval(texte)
                except (ValueError, TypeError, SyntaxError):
                    milieu = None
            if isinstance(milieu, (dict, list)):
                return _nice(milieu)
        return texte

    lignes = []
    for cle, valeur in infos.items():
        titre = _NOMS_SECTIONS.get(str(cle).upper().strip()) or _libelle(cle)
        txt = _nice(valeur)
        if txt:
            lignes.append([
                Paragraph(_xml_escape(titre), st["label"]),
                Paragraph(_xml_escape(txt), st["cellule"]),
            ])
    return lignes



def donnees_to_pdf(donnees: dict, pdf_path: str, extra: dict | None = None) -> None:
    """Génère un PDF lisible contenant les données extraites de la facture.

    Remplace l'ancien « PDF-image » : le document est un vrai récapitulatif
    avec les champs extraits, le détail des actes et le diagnostic anti-fraude.
    """
    import json as _json

    st = _style()
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)

    extra = extra or {}

    # Nettoyage : parser les champs JSON de l'analyse s'ils sont en chaîne
    analyse = extra.get("analyse") or {}
    if analyse:
        for cle in ("fraud_indicators", "recommendations", "informations", "missing_information"):
            val = analyse.get(cle)
            if isinstance(val, str) and val.strip():
                try:
                    parsed = _json.loads(val)
                    analyse[cle] = parsed
                except (ValueError, TypeError):
                    pass

    doc = SimpleDocTemplate(
        pdf_path, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title="Facture extraite",
    )

    identifiant = _s((extra.get("identifiant") or ""))
    elements = []
    elements.append(Paragraph("Facture de soins", st["titre"]))
    elements.append(Paragraph(
        f"Fournisseur : {_s(donnees.get('fournisseur'))} — N° {_s(donnees.get('numero_facture'))}"
        + (f" — Identifiant : {identifiant}" if identifiant != "—" else ""),
        st["sous"],
    ))
    elements.append(Spacer(1, 6))

    # --- Informations générales ---
    elements.append(Paragraph("Informations de la facture", st["h"]))
    def _lbl(t):
        return Paragraph(t, st["label"])

    # Devise affichée : dinar tunisien (les montants sont toujours en DT).
    devise = "DT"

    def mon(v):
        return _mon(v, devise)

    ge = Table(
        [
            [_lbl("Fournisseur"), _cell(st, donnees.get("fournisseur")), _lbl("N° de facture"), _cell(st, donnees.get("numero_facture"))],
            [_lbl("Date de facture"), _cell(st, donnees.get("date_facture")), _lbl("Devise"), _cell(st, devise)],
            [_lbl("Montant HT"), _cell(st, mon(donnees.get("montant_ht"))), _lbl("TVA"), _cell(st, mon(donnees.get("tva")))],
            [_lbl("Remise"), _cell(st, mon(donnees.get("remise"))), _lbl("Sous-total"), _cell(st, mon(donnees.get("sous_total")))],
            [_lbl("Montant TTC"), _cell(st, mon(donnees.get("montant_ttc"))), _lbl("Statut de paiement"), _cell(st, donnees.get("statut_paiement"))],
            [_lbl("Mode de paiement"), _cell(st, donnees.get("mode_paiement")), "", ""],
        ],
        colWidths=[32 * mm, 57 * mm, 32 * mm, 57 * mm],
    )
    ge.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f8")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eef2f8")),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(ge)

    # --- Patient ---
    p = donnees.get("patient") or {}
    if any(p.get(k) for k in ("nom", "prenom", "date_naissance", "numero_assure", "organisme_assurance")):
        elements.append(Paragraph("Patient / Assuré", st["h"]))
        t_patient = Table(
            [
                [_lbl("Nom"), _cell(st, p.get("nom")), _lbl("Prénom"), _cell(st, p.get("prenom"))],
                [_lbl("Né(e)"), _cell(st, p.get("date_naissance")), _lbl("N° d'assuré"), _cell(st, p.get("numero_assure"))],
                [_lbl("Adresse"), _cell(st, p.get("adresse")), _lbl("Assurance"), _cell(st, p.get("organisme_assurance"))],
            ],
            colWidths=[30 * mm, 58 * mm, 30 * mm, 58 * mm],
        )
        t_patient.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f8")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eef2f8")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(t_patient)

    # --- Professionnel ---
    pr = donnees.get("professionnel") or {}
    if any(pr.get(k) for k in ("nom", "specialite", "identifiant_professionnel", "matricule_fiscal", "adresse", "telephone")) or donnees.get("fournisseur"):
        nom_pro = _s(pr.get("nom") or donnees.get("fournisseur"))
        elements.append(Paragraph("Professionnel de santé", st["h"]))
        t_pro = Table(
            [
                [_lbl("Nom"), _cell(st, nom_pro), _lbl("Spécialité"), _cell(st, pr.get("specialite"))],
                [_lbl("Identifiant"), _cell(st, pr.get("identifiant_professionnel")), _lbl("Matricule fiscal"), _cell(st, pr.get("matricule_fiscal"))],
                [_lbl("Adresse"), _cell(st, pr.get("adresse")), _lbl("Téléphone"), _cell(st, pr.get("telephone"))],
            ],
            colWidths=[30 * mm, 58 * mm, 30 * mm, 58 * mm],
        )
        t_pro.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f8")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eef2f8")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(t_pro)

    # --- Actes ---
    actes = donnees.get("actes") or []
    if actes:
        elements.append(Paragraph("Détail des actes", st["h"]))
        rows = [["Date", "Description", "Qté", "Prix unit.", "Montant"]]
        for a in actes:
            rows.append([
                _cell(st, a.get("date")), _cell(st, a.get("description")),
                _cell(st, a.get("quantite")), _cell(st, mon(a.get("prix_unitaire"))), _cell(st, mon(a.get("montant"))),
            ])
        tab = Table(rows, colWidths=[25 * mm, 84 * mm, 14 * mm, 22 * mm, 22 * mm], hAlign="LEFT")
        tab.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a3a6b")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5fa")]),
        ]))
        elements.append(tab)

    # --- Remboursement ---
    r = donnees.get("remboursement") or {}
    if any(r.get(k) is not None for k in ("montant_total", "montant_pris_en_charge", "part_patient", "reste_a_payer", "taux_remboursement")):
        elements.append(Paragraph("Remboursement", st["h"]))
        t_remb = Table(
            [
                [_lbl("Total facturé"), _cell(st, mon(r.get("montant_total"))), _lbl("Prise en charge"), _cell(st, mon(r.get("montant_pris_en_charge")))],
                [_lbl("Part patient"), _cell(st, mon(r.get("part_patient"))), _lbl("Reste à payer"), _cell(st, mon(r.get("reste_a_payer")))],
                [_lbl("Taux remboursement"), _cell(st, r.get("taux_remboursement")), "", ""],
            ],
            colWidths=[42 * mm, 42 * mm, 42 * mm, 42 * mm],
        )
        t_remb.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f8")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eef2f8")),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        elements.append(t_remb)

    # --- Diagnostic anti-fraude ---
    if analyse:
        elements.append(Paragraph("Détection de fraude", st["h"]))
        classification = _s(analyse.get("classification"))
        elements.append(Paragraph(f"Classification : <b>{classification}</b> — Score de risque : {_s(analyse.get('risk_score'))}/100 — Confiance : {_s(analyse.get('confidence'))}%", st["sous"]))

        infos = analyse.get("informations") or analyse.get("missing_information")
        # Parser le JSON brut si c'est une chaîne
        if isinstance(infos, str) and infos.strip():
            try:
                import json as _json
                parsed = _json.loads(infos)
                if isinstance(parsed, dict):
                    infos = parsed
            except (ValueError, TypeError):
                pass
        if isinstance(infos, dict) and infos:
            lignes = _rendre_informations(infos, st)
            if lignes:
                tab = Table(lignes, colWidths=[45 * mm, 114 * mm], hAlign="LEFT")
                tab.setStyle(TableStyle([
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f8")),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]))
                elements.append(tab)
        elif isinstance(infos, str) and infos.strip() and infos.strip() != "—":
            # Ne pas afficher si c'est du JSON brut non parsable
            if not infos.strip().startswith("{"):
                elements.append(Paragraph(f"Informations : {_s(infos)}", st["cellule"]))

        indicateurs = analyse.get("fraud_indicators") or analyse.get("indicateurs") or []
        if indicateurs:
            elements.append(Paragraph("<b>Indicateurs détectés</b>", st["cellule"]))
            for i in indicateurs:
                elements.append(Paragraph(f"• {_s(i).replace('<', '&lt;').replace('>', '&gt;')}", st["cellule"]))

        recommandations = analyse.get("recommendations") or analyse.get("recommandations") or []
        if recommandations:
            elements.append(Paragraph("<b>Recommandations</b>", st["cellule"]))
            for r in recommandations:
                elements.append(Paragraph(f"• {_s(r).replace('<', '&lt;').replace('>', '&gt;')}", st["cellule"]))

        sig = analyse.get("signature_detectee")
        tam = analyse.get("tampon_detecte")
        if sig is not None or tam is not None:
            sig_txt = "Détectée" if sig else "Non détectée"
            tam_txt = "Détecté" if tam else "Non détecté"
            elements.append(Paragraph(f"Signature : {sig_txt} — Tampon : {tam_txt}", st["cellule"]))

    doc.build(elements)
