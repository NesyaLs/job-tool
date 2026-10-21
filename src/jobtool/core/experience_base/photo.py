"""Validation et normalisation d'une photo de profil, À L'ENVOI.

Pourquoi à l'envoi et pas au rendu : Typst ne sait ni rattraper une erreur d'image (il n'a
pas de try/catch), ni détecter un JPEG TRONQUÉ — il le rend en silence, avec un aplat gris
sur la partie manquante. Il intègre par ailleurs l'image TELLE QUELLE dans le PDF, si bien
qu'une photo de téléphone de 5 Mo donne un PDF de 5 Mo. On ne stocke donc qu'une image déjà
décodée et normalisée ; au rendu, il ne reste rien à vérifier.

Ce que fait `normalize_photo` : décodage COMPLET (c'est lui qui détecte un fichier tronqué),
format restreint à JPEG / PNG / WebP, refus des images animées, orientation EXIF appliquée,
recadrage carré centré, réduction à 600 × 600 px, réenregistrement en JPEG qualité 88 —
environ 45 Kio quelle que soit l'entrée.

Le 600 px n'est pas arbitraire : le plus grand disque proposé par le template fait 42 mm,
soit environ 360 ppp à cette résolution. Au-delà, on alourdirait le PDF sans rien y gagner.
"""

from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageOps, UnidentifiedImageError

#: Formats acceptés. Un format que Typst refuse (BMP, TIFF) ne doit jamais être stocké.
ALLOWED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
MAX_INPUT_BYTES = 10 * 1024 * 1024
SIDE_PX = 600
JPEG_QUALITY = 88


class PhotoError(ValueError):
    """Photo refusée, avec un message destiné à être montré à l'utilisateur."""


def normalize_photo(raw: bytes) -> tuple[str, bytes]:
    """(empreinte SHA-256, JPEG normalisé) ; PhotoError si l'image est refusée.

    L'empreinte porte sur l'image NORMALISÉE, pas sur le fichier d'origine : deux envois
    du même visage dans deux formats différents donnent la même photo stockée, et un
    brouillon qui référence une empreinte désigne exactement ce qui a été rendu.
    """
    if not raw:
        raise PhotoError("fichier vide")
    if len(raw) > MAX_INPUT_BYTES:
        raise PhotoError(
            f"fichier trop lourd ({len(raw) // 1024} Kio, maximum "
            f"{MAX_INPUT_BYTES // 1024} Kio)"
        )
    try:
        image = Image.open(io.BytesIO(raw))
        source_format = image.format
        image.load()  # décodage complet : lève OSError sur un fichier tronqué
    except UnidentifiedImageError:
        raise PhotoError("format d'image non reconnu") from None
    except Image.DecompressionBombError:
        # Fichier minuscule qui se décompresse en dizaines de millions de pixels. Pillow
        # le refuse tout seul, mais par une exception qui n'hérite PAS de OSError : sans
        # cette branche, elle remonterait en erreur 500 au lieu d'un message lisible.
        raise PhotoError("image anormalement grande une fois décompressée") from None
    except OSError as exc:
        raise PhotoError(f"image illisible ou tronquée ({exc})") from None

    if source_format not in ALLOWED_FORMATS:
        raise PhotoError(f"format {source_format} non accepté (JPEG, PNG ou WebP)")
    # Une image animée (WebP ou PNG animé) serait figée sur sa première vue, sans que
    # l'utilisateur comprenne pourquoi : on la refuse plutôt que de choisir à sa place.
    if getattr(image, "n_frames", 1) > 1:
        raise PhotoError("image animée non acceptée : envoyez une image fixe")

    # `exif_transpose` applique la rotation d'une photo de téléphone ; il renvoie une
    # Image et non l'ImageFile ouverte, d'où une variable distincte.
    upright = ImageOps.exif_transpose(image) or image
    square = ImageOps.fit(
        upright.convert("RGB"),
        (SIDE_PX, SIDE_PX),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    out = io.BytesIO()
    square.save(out, "JPEG", quality=JPEG_QUALITY, optimize=True)
    jpeg = out.getvalue()
    return hashlib.sha256(jpeg).hexdigest(), jpeg


def photo_filename(sha256: str) -> str:
    """Nom du fichier passé à Typst, dérivé de l'empreinte.

    Porter l'empreinte dans le nom fixe la VERSION de l'image : un brouillon enregistré
    désigne exactement la photo avec laquelle il a été rendu, et deux comptes ne peuvent
    pas se voir attribuer la photo l'un de l'autre par collision de nom.
    """
    return f"photo-{sha256[:12]}.jpg"
