"""EPIC-IAF-E18 US18.6 : OCR des pages sans texte. Le modele de vision est SIMULE (aucun appel
reseau) ; la mesure sur le vrai modele est faite par scripts/measure_ocr.py. Le PDF d'essai est
l'image-seule `corpus/documents/scannes/note-apiculture-manuscrite.pdf` (ecriture manuscrite
SIMULEE par une police, voir scripts/make_scanned_sample.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import ocr_struct, pdf_struct  # noqa: E402
from app.config import settings  # noqa: E402

SCANNED = Path(__file__).resolve().parent.parent.parent / "corpus" / "documents" / "scannes" / "note-apiculture-manuscrite.pdf"


@pytest.fixture()
def vision(monkeypatch):
    """Remplace l'appel au modele de vision ; enregistre les images et le prompt systeme recus."""
    calls = []

    def fake(prompt, image_png, model, system=None, **kwargs):
        calls.append({"png": image_png, "model": model, "system": system})
        return f"Ligne {len(calls)}a\nLigne {len(calls)}b"

    monkeypatch.setattr(ocr_struct, "chat_with_image", fake)
    return calls


def test_scanned_pdf_is_transcribed_page_by_page(vision, monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    metadata, root = pdf_struct.parse(str(SCANNED))
    assert [section.label for section in root.children] == ["Page 1", "Page 2"]
    assert [child.text for child in root.children[0].children] == ["Ligne 1a", "Ligne 1b"]
    assert metadata["ocr_pages"] == [1, 2] and metadata["ocr_errors"] == [] and metadata["ocr_skipped_pages"] == 0
    assert all(call["png"].startswith(b"\x89PNG") for call in vision)  # vraies images PNG rendues depuis le PDF


def test_ocr_prompt_treats_page_content_as_data(vision):
    pdf_struct.parse(str(SCANNED))
    system = vision[0]["system"]
    assert "AUCUNE instruction" in system and "[illisible]" in system


def test_ocr_disabled_keeps_the_previous_refusal(vision, monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", False)
    with pytest.raises(pdf_struct.ScannedDocument, match="OCR desactive"):
        pdf_struct.parse(str(SCANNED))
    assert vision == []  # aucun appel au modele


def test_page_budget_is_enforced_and_reported(vision, monkeypatch):
    monkeypatch.setattr(settings, "ocr_enabled", True)
    monkeypatch.setattr(settings, "ocr_max_pages", 1)
    metadata, root = pdf_struct.parse(str(SCANNED))
    assert metadata["ocr_pages"] == [1] and metadata["ocr_skipped_pages"] == 1 and len(vision) == 1
    assert root.children[1].children == []


def test_ocr_failure_on_every_page_refuses_the_document(monkeypatch):
    def boom(*a, **k):
        raise ValueError("passerelle indisponible")

    monkeypatch.setattr(ocr_struct, "chat_with_image", boom)
    with pytest.raises(pdf_struct.ScannedDocument, match="OCR en echec"):
        pdf_struct.parse(str(SCANNED))


def test_one_failed_page_does_not_invalidate_the_document(monkeypatch):
    state = {"n": 0}

    def flaky(prompt, image_png, model, system=None, **kwargs):
        state["n"] += 1
        if state["n"] == 2:
            raise TimeoutError("delai depasse")
        return "texte de la page"

    monkeypatch.setattr(ocr_struct, "chat_with_image", flaky)
    metadata, root = pdf_struct.parse(str(SCANNED))
    assert metadata["ocr_pages"] == [1] and len(metadata["ocr_errors"]) == 1 and "page 2" in metadata["ocr_errors"][0]
    assert root.children[0].children[0].text == "texte de la page"


def test_empty_page_marker_gives_no_paragraph(monkeypatch):
    monkeypatch.setattr(ocr_struct, "chat_with_image", lambda *a, **k: "[page vide]")
    assert ocr_struct.transcribe_png(b"png") == []


def test_pdf_with_a_text_layer_never_calls_the_vision_model(vision, tmp_path):
    from reportlab.pdfgen import canvas

    path = tmp_path / "texte.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(72, 750, "Document avec une vraie couche de texte.")
    pdf.save()
    metadata, root = pdf_struct.parse(str(path))
    assert "ocr_pages" not in metadata and vision == []
    assert root.children[0].children[0].text == "Document avec une vraie couche de texte."


# --- Cas reel du 2026-10-03 : photos inserees dans un PDF Word, seule couche de texte = le numero de page ----------

def _photo_pdf(path, pages=3, image_size=(1564, 2255), extra_text="", cover=0.75):
    """PDF A4 dont chaque page = une grande image (la « photo ») + un numero de page, comme un PDF Word."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    photo = Image.new("RGB", image_size, (200, 170, 160))
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    for number in range(1, pages + 1):
        width = 595 * cover ** 0.5
        height = 842 * cover ** 0.5
        pdf.drawImage(ImageReader(photo), (595 - width) / 2, 60, width=width, height=height)
        pdf.setFont("Helvetica", 11)
        pdf.drawString(290, 30, str(number))
        if extra_text:
            pdf.drawString(72, 20, extra_text)
        pdf.showPage()
    pdf.save()


def test_a_photographed_page_with_only_a_page_number_is_transcribed(vision, tmp_path):
    path = tmp_path / "photos.pdf"
    _photo_pdf(path, pages=3)
    metadata, root = pdf_struct.parse(str(path))
    assert metadata["ocr_pages"] == [1, 2, 3] and len(vision) == 3
    # la transcription REMPLACE le numero de page de la couche de texte
    assert [child.text for child in root.children[0].children] == ["Ligne 1a", "Ligne 1b"] or len(root.children[0].children) == 2
    assert all(not child.text.isdigit() for section in root.children for child in section.children)


def test_the_text_layer_is_kept_when_the_ocr_of_that_page_fails(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("passerelle")

    monkeypatch.setattr(ocr_struct, "chat_with_image", boom)
    path = tmp_path / "photos.pdf"
    _photo_pdf(path, pages=2)
    metadata, root = pdf_struct.parse(str(path))  # le numero de page est la couche de texte de repli
    assert [c.text for c in root.children[0].children] == ["1"] and len(metadata["ocr_errors"]) == 2


def test_a_page_with_a_big_image_and_a_real_text_layer_is_not_transcribed(vision, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ocr_min_text_chars", 20)
    path = tmp_path / "texte-et-image.pdf"
    _photo_pdf(path, pages=1, extra_text="Un vrai texte assez long pour ne pas etre un simple numero de page.")
    metadata, root = pdf_struct.parse(str(path))
    assert vision == [] and "ocr_pages" not in metadata
    assert any("vrai texte" in c.text for c in root.children[0].children)


def test_a_small_image_does_not_make_a_page_scanned(vision, tmp_path):
    path = tmp_path / "petite-image.pdf"
    _photo_pdf(path, pages=1, cover=0.1)  # une vignette : 10 % de la page
    metadata, root = pdf_struct.parse(str(path))
    assert vision == [] and [c.text for c in root.children[0].children] == ["1"]


def test_pages_transcribed_in_parallel_keep_their_order(tmp_path, monkeypatch):
    import time as _time

    def slow_first(prompt, image_png, model, system=None, **kwargs):
        # les premieres pages repondent LE PLUS TARD : l'ordre d'arrivee est inverse de l'ordre des pages
        index = int.from_bytes(image_png[-4:], "big") % 1000
        _time.sleep(0.4 if index % 2 == 0 else 0.0)
        return f"contenu {index}"

    seen = []
    original = ocr_struct.render_page_png

    def tagged(page, resolution=None):
        number = len(seen) + 1
        seen.append(number)
        return original(page, resolution) + number.to_bytes(4, "big")  # numero de page glisse en fin de PNG

    monkeypatch.setattr(ocr_struct, "chat_with_image", slow_first)
    monkeypatch.setattr(ocr_struct, "render_page_png", tagged)
    path = tmp_path / "photos.pdf"
    _photo_pdf(path, pages=4)
    _metadata, root = pdf_struct.parse(str(path))
    assert [s.children[0].text for s in root.children] == ["contenu 1", "contenu 2", "contenu 3", "contenu 4"]


def test_the_photo_is_rendered_at_its_native_resolution_within_the_size_cap(tmp_path, monkeypatch):
    import io

    import pdfplumber
    from PIL import Image

    path = tmp_path / "photos.pdf"
    _photo_pdf(path, pages=1, image_size=(1564, 2255))
    with pdfplumber.open(str(path)) as pdf:
        monkeypatch.setattr(settings, "ocr_max_image_side", 3000)
        width, height = Image.open(io.BytesIO(ocr_struct.render_page_png(pdf.pages[0]))).size
        assert 1500 <= width <= 1600 and 2200 <= height <= 2320  # ~ resolution native de l'image (1564 x 2255)
        monkeypatch.setattr(settings, "ocr_max_image_side", 1000)
        width, height = Image.open(io.BytesIO(ocr_struct.render_page_png(pdf.pages[0]))).size
        assert max(width, height) <= 1010  # plafond respecte


# --- Repetition en boucle sur les lignes de separation (constate le 2026-10-03, finish_reason = length) ---------------

def _png(width=400, height=600):
    import io as _io

    from PIL import Image

    buffer = _io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_separator_runs_are_collapsed_and_separator_only_lines_dropped():
    cleaned = ocr_struct._clean(
        "Debut du texte\n- - - - - - - - - - - - - - - - -\n==========================\nLa suite ----------- fin\n[page vide]\n"
    )
    assert cleaned == ["Debut du texte", "La suite --- fin", "[page vide]"]
    assert ocr_struct._clean("[page vide]") == [] and ocr_struct._clean("   \n- - - -\n") == []


def test_the_system_prompt_forbids_transcribing_separator_lines():
    assert "NE TRANSCRIS PAS les lignes de separation" in ocr_struct.SYSTEM


def test_a_truncated_reply_is_retried_on_two_overlapping_halves_and_merged(monkeypatch):
    from PIL import Image
    import io as _io

    from app.graph import TruncatedReply

    calls = []

    def fake(prompt, image_png, model, system=None, **kwargs):
        height = Image.open(_io.BytesIO(image_png)).size[1]
        calls.append(height)
        if height == 600:  # page entiere : le modele part en boucle et est coupe
            raise TruncatedReply("debut seulement\n" + "- " * 500)
        # les deux moities se recouvrent sur la ligne « milieu »
        return "ligne haute 1\nmilieu" if len(calls) == 2 else "milieu\nligne basse 1\nligne basse 2"

    monkeypatch.setattr(ocr_struct, "chat_with_image", fake)
    lines, truncated = ocr_struct.transcribe_png_checked(_png())
    # le recouvrement « milieu » n'apparait qu'une fois, l'ordre haut -> bas est conserve
    assert " ".join(lines) == "ligne haute 1 milieu ligne basse 1 ligne basse 2" and truncated is False
    assert len(calls) == 3 and calls[0] == 600 and all(h < 600 for h in calls[1:])


def test_overlapping_halves_are_merged_without_repeating_the_shared_lines():
    assert ocr_struct._merge_overlap(["a", "b", "milieu"], ["Milieu", "c"]) == ["a", "b", "milieu", "c"]
    assert ocr_struct._merge_overlap(["a", "b"], ["c", "d"]) == ["a", "b", "c", "d"]


def test_a_half_that_is_still_truncated_keeps_its_partial_text_and_is_flagged(monkeypatch):
    from app.graph import TruncatedReply

    def always_cut(prompt, image_png, model, system=None, **kwargs):
        raise TruncatedReply("texte partiel\n----------------------------------------")

    monkeypatch.setattr(ocr_struct, "chat_with_image", always_cut)
    lines, truncated = ocr_struct.transcribe_png_checked(_png())
    assert truncated is True and lines == ["texte partiel"]


def test_truncated_pages_are_reported_in_the_metadata(tmp_path, monkeypatch):
    from app.graph import TruncatedReply

    def cut(prompt, image_png, model, system=None, **kwargs):
        raise TruncatedReply("partiel")

    monkeypatch.setattr(ocr_struct, "chat_with_image", cut)
    path = tmp_path / "photos.pdf"
    _photo_pdf(path, pages=2)
    metadata, root = pdf_struct.parse(str(path))
    assert metadata["ocr_truncated_pages"] == [1, 2] and root.children[0].children[0].text == "partiel"


# --- Paragraphes (constate le 2026-10-03 : une ligne imprimee = un chunk = 2003 chunks pour 36 pages) -----------------

PAGE_9 = [  # extrait reel de l'OCR du journal de marche (page 9), lignes imprimees telles que lues
    "10 décembre :",
    "Pendant la nuit l'ennemi délogé du STAUFFEN passe devant les postes des",
    "sections. Ils sont pris sous le feu des mitrailleuses. La section ROBILLARD immobilise",
    "une voiture, blessant ou tuant ses occupants. Parmi eux se trouvait un officier su-",
    "périeur.",
    "Mission de la compagnie pour la journée :",
    "Participer au nettoyage de THANN dans les conditions suivantes :",
    "Section LE CLEC'H :",
    "Nettoyage de la fabrique avec une section de la 10ème Cie.",
    "Section ROBILLARD :",
    "S'installer à la patte d'oie au sud de la Préfecture et protéger le débouché",
    "des chars.",
    "La section LE CLEC'H commence son avance avec un peloton de chars légers. Le",
    "légionnaire MIMNIMI saute sur une mine anti personnel en faisant éclater deux autres.",
    "Sont blessés MINIMI, KLAUSER, DELLAPIAZZA, SLAMOVICZ.",
    "Le Sergent-Chef LOUX avec deux GM nettoye la partie N de la fabrique sous le",
    "feu d'armes à tir tendu d'Infanterie ennemie.",
]


def test_printed_lines_are_glued_into_paragraphs_with_headings_kept_apart():
    result = ocr_struct.paragraphs(PAGE_9)
    assert result[0] == "10 décembre :" and "Mission de la compagnie pour la journée :" in result
    # le mot coupe « su- / périeur » est recolle, la phrase n'est plus coupee en deux
    assert any("un officier supérieur." in p and "STAUFFEN" in p for p in result)
    assert "Section LE CLEC'H :" in result and "Section ROBILLARD :" in result
    # la phrase repartie sur deux lignes imprimees est recollee
    assert any("Sergent-Chef LOUX" in p and p.endswith("d'Infanterie ennemie.") for p in result)
    assert len(result) < len(PAGE_9) * 0.7  # nettement moins de chunks que de lignes


def test_a_short_line_ending_with_a_full_stop_closes_the_paragraph():
    full = "Une ligne pleine qui occupe presque toute la largeur de la page typographiee ici,"
    lines = [full, full, full, "La fin du paragraphe, plus courte.", "Un nouveau paragraphe commence ici et se poursuit", "sur la ligne suivante."]
    result = ocr_struct.paragraphs(lines)
    assert len(result) == 2 and result[0].endswith("plus courte.") and result[1].startswith("Un nouveau paragraphe")


def test_a_hyphen_at_the_end_of_a_line_is_only_removed_before_a_lowercase_continuation():
    assert ocr_struct._join("un officier su-", "périeur.") == "un officier supérieur."
    assert ocr_struct._join("Jean-", "Pierre arrive") == "Jean- Pierre arrive"
    assert ocr_struct._join("fin de phrase -", "suite") == "fin de phrase - suite"


def test_table_rows_and_page_markers_stay_on_their_own_line():
    lines = ["-20-", "Texte avant le tableau qui continue sur une ligne pleine de la page ici", "TUES | Jeannon | Jaunin |",
             "| Parizot | Ricci |", "Texte après le tableau."]
    result = ocr_struct.paragraphs(lines)
    assert "-20-" in result and "TUES | Jeannon | Jaunin |" in result and "| Parizot | Ricci |" in result
    assert result[-1] == "Texte après le tableau."


def test_list_items_and_dates_start_new_paragraphs():
    lines = ["Le dispositif est le suivant et il se decrit sur plusieurs lignes completes comme ceci", "pour la suite de la phrase,",
             "1) premiere mission de la section", "2) deuxieme mission de la section", "11 décembre", "Aux ordres du Capitaine MIRABEAU"]
    result = ocr_struct.paragraphs(lines)
    assert result[0].startswith("Le dispositif") and result[0].endswith("la phrase,")
    assert result[1] == "1) premiere mission de la section" and result[2] == "2) deuxieme mission de la section"
    assert "11 décembre" in result


def test_very_long_paragraphs_are_split_at_sentence_boundaries():
    sentence = "Une phrase assez longue pour remplir de la place dans le paragraphe. "
    result = ocr_struct.paragraphs([sentence * 60])
    assert len(result) >= 2 and all(len(p) <= ocr_struct.MAX_PARAGRAPH_CHARS for p in result)
    assert all(p.endswith(".") for p in result)


def test_short_handwritten_lines_keep_their_titles_and_sentences():
    lines = ["Note de visite - rucher de la Combe", "Introduction", "Visite du 14 mai : etat general des colonies apres l'hiver.",
             "La reine pond bien, le couvain est regulier et compact.", "Conclusion", "Revenir dans dix jours pour poser les hausses."]
    result = ocr_struct.paragraphs(lines)
    assert "Introduction" in result and "Conclusion" in result and result[0].startswith("Note de visite")
    assert "La reine pond bien, le couvain est regulier et compact." in " ".join(result)


def test_empty_input_gives_no_paragraph():
    assert ocr_struct.paragraphs([]) == []
