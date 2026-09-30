"""
The image-analysis "brain".

Two responsibilities, per the assignment:
  1. Extract visual attributes from the product photo (type, colours, prints,
     logos, text, material, shape) -> used to build better search queries.
  2. Compare a candidate video's thumbnail against the product photo and
     produce a 0-100 match score with a human-readable reason.

Provider is pluggable via VISION_PROVIDER:
  - "local_clip": open_clip (ViT-B/32) image embeddings + cosine similarity.
    No API key, runs locally, good enough for visual similarity ranking.
  - "openai": GPT-4o-vision style call that returns attributes + a judged
    score with a reason in one shot. Higher accuracy on "same product, worn
    by a different model" cases, but costs tokens per video.

We default to local_clip so the project runs out of the box without any key,
and document the trade-off in the README (see: Image-analysis brain section).
"""
from __future__ import annotations

import base64
import io

import httpx
from PIL import Image

from app.core.config import settings

_clip_model = None
_clip_preprocess = None
_clip_tokenizer = None


def _load_clip():
    global _clip_model, _clip_preprocess, _clip_tokenizer
    if _clip_model is None:
        import open_clip

        _clip_model, _, _clip_preprocess = open_clip.create_model_and_transforms(
            "ViT-B-32", pretrained="laion2b_s34b_b79k"
        )
        _clip_tokenizer = open_clip.get_tokenizer("ViT-B-32")
        _clip_model.eval()
    return _clip_model, _clip_preprocess, _clip_tokenizer


async def fetch_image(url: str) -> Image.Image:
    """Accepts a normal http(s) URL, or a `data:image/...;base64,...` URI --
    the latter is how a user-uploaded product photo reaches the pipeline
    (see SearchCreateRequest.image_base64 / pipeline.py), so no separate
    file-storage layer is needed for this take-home."""
    if url.startswith("data:"):
        _, _, encoded = url.partition(",")
        raw = base64.b64decode(encoded)
        return Image.open(io.BytesIO(raw)).convert("RGB")

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
    return Image.open(io.BytesIO(resp.content)).convert("RGB")


async def analyze_product_image(image_url: str) -> dict:
    """Extract visual attributes used to build search queries/hashtags."""
    if settings.vision_provider == "openai":
        return await _analyze_with_openai(image_url)
    return await _analyze_with_clip_zero_shot(image_url)


async def score_video_frame(product_image_url: str, candidate_thumbnail_url: str) -> tuple[int, str]:
    """Returns (match_score 0-100, human_readable_reason)."""
    if settings.vision_provider == "openai":
        return await _score_with_openai(product_image_url, candidate_thumbnail_url)
    return await _score_with_clip(product_image_url, candidate_thumbnail_url)


# ---------------------------------------------------------------- local_clip

_ATTRIBUTE_LABELS = {
    "product_type": ["t-shirt", "hoodie", "sneakers", "bottle", "chocolate bar", "bag", "dress", "jacket"],
    "color": ["black", "white", "red", "blue", "green", "beige", "pink", "multicolor"],
    "pattern": ["plain", "graphic print", "striped", "floral", "logo print", "text print"],
    "material": ["cotton", "leather", "plastic", "metal", "denim", "glass"],
}


async def _analyze_with_clip_zero_shot(image_url: str) -> dict:
    import torch

    model, preprocess, tokenizer = _load_clip()
    image = await fetch_image(image_url)
    image_input = preprocess(image).unsqueeze(0)

    attributes: dict[str, str] = {}
    with torch.no_grad():
        image_features = model.encode_image(image_input)
        image_features /= image_features.norm(dim=-1, keepdim=True)

        for attr_name, labels in _ATTRIBUTE_LABELS.items():
            text_tokens = tokenizer([f"a photo of a {label} product" for label in labels])
            text_features = model.encode_text(text_tokens)
            text_features /= text_features.norm(dim=-1, keepdim=True)
            similarity = (image_features @ text_features.T).squeeze(0)
            best_idx = similarity.argmax().item()
            attributes[attr_name] = labels[best_idx]

    return {
        "product_type": attributes["product_type"],
        "colors": [attributes["color"]],
        "pattern": attributes["pattern"],
        "material": attributes["material"],
        "logos_or_text": None,  # local_clip can't read text reliably; OCR could be added here
        "search_queries": _build_queries(attributes),
        "hashtags": _build_hashtags(attributes),
    }


def _build_queries(attrs: dict) -> list[str]:
    base = f"{attrs['color']} {attrs['pattern']} {attrs['product_type']}"
    return [base, f"{attrs['product_type']} {attrs['color']}", attrs["product_type"]]


def _build_hashtags(attrs: dict) -> list[str]:
    return [
        f"#{attrs['product_type'].replace(' ', '')}",
        f"#{attrs['color']}{attrs['product_type'].replace(' ', '')}",
        f"#{attrs['pattern'].replace(' ', '')}",
    ]


async def _score_with_clip(product_image_url: str, candidate_url: str) -> tuple[int, str]:
    import torch
    import torch.nn.functional as F

    model, preprocess, _ = _load_clip()

    product_img = preprocess((await fetch_image(product_image_url))).unsqueeze(0)
    candidate_img = preprocess((await fetch_image(candidate_url))).unsqueeze(0)

    with torch.no_grad():
        product_feat = model.encode_image(product_img)
        candidate_feat = model.encode_image(candidate_img)
        similarity = F.cosine_similarity(product_feat, candidate_feat).item()

    # cosine similarity for CLIP image-image pairs of the *same* object typically
    # lands ~0.75-0.95; unrelated images ~0.3-0.5. Rescale to a 0-100 UI score.
    score = max(0, min(100, round((similarity - 0.3) / (0.95 - 0.3) * 100)))

    if score >= 80:
        reason = "Strong visual match: same colour, print and product shape as the product photo."
    elif score >= 55:
        reason = "Likely match: similar colour/shape, but angle or background differs from the product photo."
    else:
        reason = "Weak visual match: colours/shape differ noticeably from the product photo."

    return score, reason


# ------------------------------------------------------------------ openai

async def _analyze_with_openai(image_url: str) -> dict:
    """
    Uses an OpenAI vision-capable chat model to return structured attributes.
    Requires OPENAI_API_KEY. Kept as a thin, swappable alternative to CLIP.
    """
    import json

    prompt = (
        "Look at this product photo. Return strict JSON with keys: "
        "product_type, colors (list), pattern, material, logos_or_text, "
        "search_queries (list of 3 short e-commerce/social search phrases), "
        "hashtags (list of 3, no spaces, include #)."
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": image_url}},
                        ],
                    }
                ],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)


async def _score_with_openai(product_image_url: str, candidate_url: str) -> tuple[int, str]:
    import json

    prompt = (
        "Image 1 is the reference product. Image 2 is a still from a video. "
        "Does image 2 show the exact same product (not just a similar item)? "
        "Return strict JSON: {\"score\": <0-100 integer>, \"reason\": \"<one short sentence>\"}."
    )

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": product_image_url}},
                            {"type": "image_url", "image_url": {"url": candidate_url}},
                        ],
                    }
                ],
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        result = json.loads(content)
        return int(result["score"]), result["reason"]
