"""How much memory does the app + embedding model need? (Render free = 512 MB)"""
import resource
import sys
import time


def peak_mb():
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / (1024 * 1024) if sys.platform == "darwin" else r / 1024   # macOS: bytes, Linux: KB


import api  # noqa: E402  (loads pandas + builds the demo data, like Render does)
print(f"App alone:            {peak_mb():6.0f} MB")

from fastembed import TextEmbedding  # noqa: E402

t = time.time()
model = TextEmbedding("sentence-transformers/all-MiniLM-L6-v2", cache_dir="models")
print(f"Model loaded in       {time.time() - t:6.1f} s")

questions = [
    "how did lattes do last week",
    "what's my busiest time of day",
    "compare this month with last month",
    "anything unusual lately?",
    "top sellers this month",
]
t = time.time()
vectors = list(model.embed(questions))
per_q = (time.time() - t) / len(questions) * 1000
print(f"Each question takes   {per_q:6.0f} ms  ({len(vectors[0])} numbers per question)")
print(f"App + model (peak):   {peak_mb():6.0f} MB   <- must stay well under 512")

# Worst case: chat model loaded AND the heaviest dashboard requests running
from anomalies import detect  # noqa: E402
from insights import insights  # noqa: E402

detect(60)
insights(90)
print(f"+ dashboard requests: {peak_mb():6.0f} MB   <- realistic worst case on Render")
