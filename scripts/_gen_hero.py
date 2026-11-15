"""One-shot helper: generate hero.png for the README via Gemini 3 Pro Image.

Reads GOOGLE_GENAI_API_KEY from environment. Writes docs/hero.png.
This file is intentionally outside the package — it's not part of the
shipping product, just a build helper.
"""

import io
import os
import sys

from google import genai
from google.genai import types
from PIL import Image


PROMPT = """A wide cinematic 16:9 hero image for an open-source SEO crawler called "crawlforge".

Composition: a top-down isometric view of an abstract data network. Hundreds of softly glowing connected nodes form a sprawling website sitemap, with delicate cyan and violet light beams traveling along the edges to suggest a crawler discovering, analyzing and validating links. In the foreground left, a translucent rectangular HUD panel shows three tiny rows of clean monospaced text: "269 rules" / "18 categories" / "MCP native", with a small live status dot. In the background right, an elegant minimal terminal window with a single command line in soft cyan: "$ crawlforge run https://example.com" — and a discreet glowing prompt cursor.

Style: ultra-modern dark-mode developer aesthetic, inspired by Vercel, Linear and Stripe's 2026 documentation hero images. Deep near-black background with subtle gradient (#0A0B14 to #050610). Accent colors: a cool cyan (#22D3EE) and a soft violet (#A78BFA), used sparingly only for the active links and the panel borders. Subtle volumetric atmosphere, very fine grain, faint bokeh from out-of-focus distant nodes. Crisp depth of field on the central data graph.

Typography: lowercase wordmark "crawlforge" in a refined monospace font (think JetBrains Mono or Berkeley Mono), placed bottom-left, small, with low contrast so it does not dominate. No taglines, no other text apart from what is mentioned.

Do NOT include any brand logos, real company names, or recognizable product UI. No people, no faces, no animals, no actual frog or spider creature, no skeuomorphic insects. The "crawler" idea must be conveyed only through the abstract network of light and motion.

Render quality: 8K studio quality, ultra sharp, absolutely no blur on the foreground nodes. Aspect ratio 16:9. Suitable as a GitHub README banner."""


def main() -> int:
    api_key = os.environ.get("GOOGLE_GENAI_API_KEY", "")
    if not api_key:
        print("ERROR: GOOGLE_GENAI_API_KEY not set")
        return 1

    output_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "docs", "hero.png"
    )
    output_path = os.path.abspath(output_path)

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model="gemini-3-pro-image-preview",
        contents=PROMPT,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            safety_settings=[
                types.SafetySetting(
                    category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    threshold="BLOCK_ONLY_HIGH",
                )
            ],
        ),
    )

    for part in response.candidates[0].content.parts:
        if part.inline_data:
            img = Image.open(io.BytesIO(part.inline_data.data))
            img.save(output_path)
            print(f"OK {output_path} {img.size[0]}x{img.size[1]}")
            return 0
        if part.text:
            print(f"text: {part.text[:300]}")
    print("no image returned")
    return 1


if __name__ == "__main__":
    sys.exit(main())
