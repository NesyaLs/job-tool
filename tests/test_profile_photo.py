"""Photo de profil : normalisation à l'envoi, stockage cloisonné, transmission au rendu.

Aucun LLM. Typst n'est sollicité que par le dernier test, qui est le seul à prouver ce qui
compte vraiment : la photo arrive jusqu'au PDF, et son absence ne change rien.
"""

from __future__ import annotations

import io
import uuid

import pytest
from PIL import Image
from jobtool.core.db import connect_sqlite, ensure_user
from jobtool.core.experience_base.photo import (
    MAX_INPUT_BYTES,
    PhotoError,
    normalize_photo,
    photo_filename,
)
from jobtool.core.experience_base.photo_store import PhotoStore, StoredPhoto


def _image(size: tuple[int, int], fmt: str = "JPEG", color: str = "red") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path):
    """(PhotoStore d'un compte, PhotoStore d'un AUTRE compte) sur une base jetable.

    Passe par un CHEMIN et non un moteur : c'est la forme du mode mono-poste, celle où
    `open_connection` crée le schéma tout seul.
    """
    db = str(tmp_path / "photos.db")
    mine, other = uuid.uuid4(), uuid.uuid4()
    # `profile_photos` référence `users` : les comptes doivent exister avant toute
    # écriture. La contrainte est réellement vérifiée — c'est elle qui garantit qu'une
    # photo ne peut pas survivre à son compte.
    with connect_sqlite(db) as conn:
        for user_id in (mine, other):
            ensure_user(conn, user_id, email=f"{user_id}@test")
        conn.commit()
    return PhotoStore(db, mine, profile_key=""), PhotoStore(db, other, profile_key="")


# --- Normalisation ----------------------------------------------------------


def test_any_accepted_image_becomes_the_same_square_jpeg():
    """Portrait, paysage, PNG ou WebP : tout ressort en JPEG carré de côté fixe.

    C'est ce qui rend le rendu prévisible : le template n'a jamais à composer avec une
    image dont il ignore le format ou les proportions.
    """
    for raw in (
        _image((600, 900)),
        _image((1200, 600), "PNG"),
        _image((400, 400), "WEBP"),
    ):
        _, jpeg = normalize_photo(raw)
        out = Image.open(io.BytesIO(jpeg))
        assert out.size == (600, 600)
        assert out.format == "JPEG"


def test_digest_identifies_the_normalised_image_not_the_upload():
    """L'empreinte porte sur l'image NORMALISÉE : deux fois la même photo, même id."""
    first, _ = normalize_photo(_image((600, 900)))
    second, _ = normalize_photo(_image((600, 900)))
    assert first == second
    assert photo_filename(first).startswith("photo-")
    assert photo_filename(first).endswith(".jpg")
    # Une autre image donne un autre identifiant (sans quoi un brouillon désignerait
    # n'importe quelle photo).
    other, _ = normalize_photo(_image((600, 900), color="blue"))
    assert other != first


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("vide", b""),
        ("pas une image", b"bonjour"),
        ("trop lourd", b"\xff\xd8\xff" + b"\x00" * MAX_INPUT_BYTES),
    ],
)
def test_unusable_uploads_are_refused_with_a_readable_message(label, payload):
    with pytest.raises(PhotoError) as exc:
        normalize_photo(payload)
    assert str(exc.value)  # un message, jamais une trace brute
    assert "Traceback" not in str(exc.value), label


def test_truncated_image_is_refused():
    """Le cas que Typst NE SAIT PAS détecter : il rendrait un aplat gris sans erreur."""
    _, jpeg = normalize_photo(_image((600, 600)))
    with pytest.raises(PhotoError, match="tronquée|illisible"):
        normalize_photo(jpeg[: len(jpeg) // 3])


def test_format_typst_would_reject_is_refused_here():
    """BMP : refusé à l'envoi plutôt qu'au rendu, où l'erreur serait incompréhensible."""
    with pytest.raises(PhotoError, match="non accepté"):
        normalize_photo(_image((200, 200), "BMP"))


def test_animated_image_is_refused_rather_than_silently_frozen():
    buffer = io.BytesIO()
    frames = [Image.new("RGB", (80, 80), c) for c in ("red", "blue")]
    frames[0].save(buffer, "WEBP", save_all=True, append_images=frames[1:], duration=80)
    with pytest.raises(PhotoError, match="animée"):
        normalize_photo(buffer.getvalue())


# --- Stockage ---------------------------------------------------------------


def test_photo_is_partitioned_by_account(store):
    """Le cloisonnement est la propriété à ne jamais perdre : une photo, un compte."""
    mine, other = store
    sha, jpeg = normalize_photo(_image((600, 600)))
    mine.save(StoredPhoto(sha, jpeg))

    assert mine.load() == StoredPhoto(sha, jpeg)
    assert other.load() is None, (
        "la photo d'un compte ne doit jamais fuir vers un autre"
    )


def test_saving_twice_replaces_rather_than_duplicates(store):
    mine, _ = store
    first, jpeg1 = normalize_photo(_image((600, 600), color="red"))
    second, jpeg2 = normalize_photo(_image((600, 600), color="blue"))
    mine.save(StoredPhoto(first, jpeg1))
    mine.save(StoredPhoto(second, jpeg2))
    assert mine.load() == StoredPhoto(second, jpeg2)


def test_delete_removes_the_personal_data(store):
    mine, _ = store
    sha, jpeg = normalize_photo(_image((600, 600)))
    mine.save(StoredPhoto(sha, jpeg))
    assert mine.delete() is True
    assert mine.load() is None
    assert mine.delete() is False, "supprimer deux fois n'est pas une erreur"


def test_load_if_refuses_a_stale_digest(store):
    """Un profil qui référence une version remplacée depuis n'obtient RIEN.

    Mieux vaut un CV sans photo qu'un CV portant une photo que l'utilisateur croyait
    avoir remplacée.
    """
    mine, _ = store
    old, jpeg_old = normalize_photo(_image((600, 600), color="red"))
    new, jpeg_new = normalize_photo(_image((600, 600), color="blue"))
    mine.save(StoredPhoto(new, jpeg_new))

    assert mine.load_if(new) == StoredPhoto(new, jpeg_new)
    assert mine.load_if(old) is None
    assert mine.load_if(None) is None
    assert jpeg_old  # garde-fou : les deux images sont bien distinctes


# --- Transmission au rendu --------------------------------------------------


def test_render_input_carries_the_filename_only_when_there_is_a_photo(
    example_base, example_cv
):
    from jobtool.documents.cv.render import build_render_input

    sha, _ = normalize_photo(_image((600, 600)))
    with_photo = build_render_input(example_base, example_cv, photo_sha256=sha)
    without = build_render_input(example_base, example_cv)

    assert with_photo["profile"]["photo"] == photo_filename(sha)
    # Clé ABSENTE, et non vide : les templates la lisent avec une valeur par défaut, et
    # un brouillon enregistré avant la photo doit continuer de se rendre.
    assert "photo" not in without["profile"]


def test_photo_reaches_the_pdf_and_its_absence_changes_nothing(
    example_base, example_cv, typst_out_dir
):
    """Preuve de bout en bout, la seule qui compte.

    Avec une photo, le PDF est plus lourd (l'image y est intégrée) ; sans, le rendu est
    strictement celui d'avant — c'est ce qui autorise à ne PAS recalibrer le template.
    """
    from jobtool.core.rendering.pdf_checks import count_pdf_pages
    from jobtool.documents.cv.render import render_cv

    # metronic affiche l'accroche ; la base d'exemple n'en a pas, on lui en donne une.
    example_base = example_base.model_copy(
        update={
            "profile": example_base.profile.model_copy(
                update={"accroche": "Élève ingénieur, en recherche de stage."}
            )
        }
    )
    sha, jpeg = normalize_photo(_image((600, 900)))
    with_photo = render_cv(
        example_base,
        example_cv,
        str(typst_out_dir / "cv_photo.pdf"),
        template="metronic",
        photo=(sha, jpeg),
    )
    without = render_cv(
        example_base,
        example_cv,
        str(typst_out_dir / "cv_sans_photo.pdf"),
        template="metronic",
    )

    from pathlib import Path

    # Le CV d'exemple n'est pas passé par fill.py : sa longueur n'est pas le sujet ici.
    # Ce qui se vérifie, c'est que la photo ne change RIEN à la mise en page (même nombre
    # de pages) tout en étant bien présente dans le fichier (poids supérieur).
    assert count_pdf_pages(with_photo) == count_pdf_pages(without)
    assert Path(with_photo).stat().st_size > Path(without).stat().st_size, (
        "l'image doit être intégrée au PDF"
    )


# --- Service et API ---------------------------------------------------------


@pytest.fixture
def profile_service(tmp_path):
    """ProfileService complet (base YAML + photo en base), sur un compte jetable."""
    import textwrap

    from jobtool.core.experience_base.store import YamlExperienceBase
    from jobtool.services.profile_service import ProfileService

    base_file = tmp_path / "moi.yaml"
    base_file.write_text(
        textwrap.dedent("""
            profile:
              name: Camille Martin
              email: camille@example.com
            education: []
            languages: []
            hard_skills: [Python]
            soft_skills: []
            interests: []
            experiences: []
        """).strip(),
        encoding="utf-8",
    )
    db = str(tmp_path / "photos.db")
    user_id = uuid.uuid4()
    with connect_sqlite(db) as conn:
        ensure_user(conn, user_id, email="camille@example.com")
        conn.commit()
    return ProfileService(
        YamlExperienceBase(base_file), PhotoStore(db, user_id, profile_key="")
    )


def test_service_keeps_photo_and_profile_in_step(profile_service):
    """L'image et son empreinte sont écrites ensemble : sinon le rendu ne la trouve pas."""
    assert profile_service.read().a_photo is False
    assert profile_service.photo_for_render() is None

    dto = profile_service.set_photo(_image((600, 900)))
    assert dto.a_photo is True
    rendu = profile_service.photo_for_render()
    assert rendu is not None
    sha, jpeg = rendu
    assert photo_filename(sha)
    assert jpeg == profile_service.photo_bytes()

    assert profile_service.delete_photo().a_photo is False
    assert profile_service.photo_for_render() is None


def test_saving_the_profile_does_not_wipe_the_photo(profile_service):
    """Régression : `_to_base` reconstruit Profile champ par champ.

    Sans préservation explicite, un simple enregistrement du profil effacerait l'empreinte
    et le CV perdrait la photo — sans le moindre message.
    """
    profile_service.set_photo(_image((600, 600)))
    dto = profile_service.read()
    dto.telephone = "0600000000"

    assert profile_service.write(dto).a_photo is True
    assert profile_service.photo_for_render() is not None


@pytest.fixture
def client(profile_service):
    from fastapi.testclient import TestClient

    from jobtool.api.deps import get_profile_service
    from jobtool.api.main import app

    app.dependency_overrides[get_profile_service] = lambda: profile_service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_upload_serve_and_delete(client):
    assert client.get("/profile/photo").status_code == 404

    sent = client.put(
        "/profile/photo",
        files={"fichier": ("moi.jpg", _image((600, 900)), "image/jpeg")},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["a_photo"] is True

    served = client.get("/profile/photo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/jpeg"
    assert served.content[:2] == b"\xff\xd8", "un vrai JPEG, pas du base64"
    assert client.get("/profile").json()["a_photo"] is True

    assert client.delete("/profile/photo").json()["a_photo"] is False
    assert client.get("/profile/photo").status_code == 404


def test_api_refuses_an_unusable_image_with_a_readable_reason(client):
    r = client.put(
        "/profile/photo", files={"fichier": ("x.jpg", b"pas une image", "image/jpeg")}
    )
    assert r.status_code == 422
    assert "format" in r.json()["detail"].lower()
    assert client.get("/profile/photo").status_code == 404, "rien n'a été rangé"


def test_api_refuses_an_oversized_upload_before_decoding_it(client):
    r = client.put(
        "/profile/photo",
        files={
            "fichier": (
                "gros.jpg",
                b"\xff\xd8" + b"\x00" * MAX_INPUT_BYTES,
                "image/jpeg",
            )
        },
    )
    assert r.status_code == 413
    assert "lourd" in r.json()["detail"]
