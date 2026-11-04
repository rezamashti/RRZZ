import math
from functools import lru_cache

import torch
import open_clip
from PIL import Image

MODEL_NAME = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Search phrases are compared against the style of the samples using CLIP.
SEARCH_PHRASES = [
    "aesthetic portrait photography", "moody portrait photography", "minimalist portrait photo",
    "cinematic portrait photography", "black and white portrait photography", "soft light portrait",
    "street photography aesthetic", "fashion editorial photography", "silhouette photography",
    "nature aesthetic photography", "mountain landscape photography", "ocean and beach photography",
    "forest photography", "flowers macro photography", "botanical aesthetic photography",
    "sunset sky photography", "clouds sky aesthetic", "night sky stars photography",
    "minimal architecture photography", "minimalist interior photography", "abstract aesthetic photography",
    "vintage film photography", "analog film aesthetic", "urban night photography",
    "cute animal portrait photography", "artistic pet photography", "travel photography aesthetic",
    "cozy lifestyle photography", "minimal still life photography", "colorful abstract art",
    "dark academia aesthetic photography", "pastel aesthetic photography", "dark moody aesthetic photo",
    "warm golden hour photography", "minimalist black and white photography", "texture pattern photography",
]

@lru_cache(maxsize=1)
def load_model():
    model, _, preprocess = open_clip.create_model_and_transforms(
        MODEL_NAME, pretrained=PRETRAINED
    )
    tokenizer = open_clip.get_tokenizer(MODEL_NAME)
    model = model.to(DEVICE).eval()
    return model, preprocess, tokenizer


def feature_vector(path):
    model, preprocess, _ = load_model()
    with Image.open(path) as im:
        image = im.convert("RGB")
        tensor = preprocess(image).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        embedding = model.encode_image(tensor)
        embedding = embedding / embedding.norm(dim=-1, keepdim=True)
    return embedding[0].detach().cpu().tolist()


def similarity(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return max(-1.0, min(1.0, dot / (na * nb))) if na and nb else 0.0


@lru_cache(maxsize=1)
def _text_features():
    model, _, tokenizer = load_model()
    labels = [
        "a beautiful aesthetic profile picture", "a high quality artistic portrait photograph",
        "a minimal aesthetic nature photograph", "a beautiful flower or botanical photograph",
        "a beautiful sky, clouds or sunset photograph", "a cute animal portrait photograph",
        "a minimalist abstract aesthetic artwork", "a blurry low quality photograph",
        "a screenshot of an app or website", "a photograph with a large watermark or text",
    ]
    tokens = tokenizer(labels).to(DEVICE)
    with torch.no_grad():
        vectors = model.encode_text(tokens)
        vectors = vectors / vectors.norm(dim=-1, keepdim=True)
    return labels, vectors.detach().cpu()


def profile_suitability(path):
    image_vec = torch.tensor(feature_vector(path), dtype=torch.float32)
    labels, text_vecs = _text_features()
    scores = torch.mv(text_vecs, image_vec)
    good = max(float(scores[i]) for i in range(7))
    bad = max(float(scores[i]) for i in range(7, 10))
    return good - bad, labels[int(torch.argmax(scores))]


def suggest_search_queries(sample_vectors, limit=4):
    """Pick search phrases whose CLIP text embeddings best match sample-photo style."""
    if not sample_vectors:
        return SEARCH_PHRASES[:limit]
    model, _, tokenizer = load_model()
    centroid = torch.tensor(sample_vectors, dtype=torch.float32).mean(dim=0)
    centroid = centroid / centroid.norm()
    tokens = tokenizer(SEARCH_PHRASES).to(DEVICE)
    with torch.no_grad():
        text_vecs = model.encode_text(tokens)
        text_vecs = text_vecs / text_vecs.norm(dim=-1, keepdim=True)
        scores = torch.mv(text_vecs, centroid.to(DEVICE)).detach().cpu()
    indices = torch.argsort(scores, descending=True).tolist()
    chosen = []
    for i in indices:
        phrase = SEARCH_PHRASES[i]
        # Avoid returning near-identical phrase variants.
        if all(len(set(phrase.split()) & set(old.split())) < 3 for old in chosen):
            chosen.append(phrase)
        if len(chosen) >= limit:
            break
    return chosen
