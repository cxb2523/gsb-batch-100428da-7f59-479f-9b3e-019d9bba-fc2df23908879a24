"""Image incorrectly sized.

# TODO(schema): requires display size (rendered) vs natural size comparison.
The schema captures `width`/`height` from HTML attributes but not the actual
display size after CSS layout, nor the natural intrinsic dimensions of the
image file. Detection requires either rendered DOM measurement
(getBoundingClientRect) or fetching the image bytes and reading EXIF/PNG header.
"""

from collections.abc import Iterable

from duckdb import DuckDBPyConnection

from crawlforge.rules import Rule, register_rule
from crawlforge.rules._issue import Issue


@register_rule
class ImageIncorrectlySized(Rule):
    id = "image_incorrectly_sized"
    name = "Image Incorrectly Sized"
    category = "Images"
    severity = "info"
    description = (
        "Imagen servida en dimensiones distintas a su tamaño real de display."
    )
    fix_guidance = (
        "Sirve la imagen en una resolución cercana a su tamaño de display "
        "(considerando 1x/2x densidades). Usa srcset/sizes para responsive."
    )
    references = [
        "https://web.dev/serve-images-with-correct-dimensions/",
    ]
    enabled_by_default = False
    requires_rendered_html = True

    def check(self, con: DuckDBPyConnection) -> Iterable[Issue]:
        # placeholder query — needs intrinsic image dimensions and rendered display size
        return iter([])
