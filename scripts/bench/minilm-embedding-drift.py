#!/usr/bin/env python3
"""minilm-embedding-drift — prove the description index still matches after a dependency bump.

    # on fry, dump a sample of (text, stored vector) pairs
    podman exec postgres psql -U murrix -d murrix -tAc "\\
      SELECT json_agg(row_to_json(t)) FROM ( \\
        SELECT e.id, convert_from(lo_get(b.loid), 'UTF8') AS text, d.embedding::text AS vector \\
          FROM primary_root_embeddings_description_data d \\
          JOIN primary_root_nodes_lnk el ON el.child_id = d.id AND el.name = 'embedding' \\
          JOIN primary_root_blobs b ON b.node_id = el.parent_id \\
         LIMIT 8) t;" > /var/tmp/drift-sample.json

    # then, inside the NEW image
    podman run --rm -v /var/tmp:/w:ro --entrypoint python3 murrix /w/minilm-embedding-drift.py /w/drift-sample.json

WHY THIS EXISTS. Every description embedding in pgvector was produced by
`paraphrase-multilingual-MiniLM-L12-v2` through sentence-transformers. If a version bump changes
its pooling, normalisation or tokenizer, the model still loads, still returns 384 floats, and
still answers every query -- but the new vectors no longer sit in the same space as the millions
already stored, and semantic search quietly gets worse. Nothing fails, nothing logs, and the only
symptom is that results feel off.

That is the single most dangerous regression in unpinning `transformers` (4.49.0 -> 5.16.1, which
drags sentence-transformers with it), because every other failure in that change is loud. So this
is run against the built image BEFORE it is deployed, and a fail means pin differently rather
than deploy and find out.

Exits non-zero if any pair drifts past the threshold.
"""
import json
import sys

# Cosine below this means the model moved. Deliberately brutal: re-encoding the same text with
# the same model must be bit-comparable up to float noise, so anything short of ~1.0 is a real
# behavioural change and not a rounding artefact.
THRESHOLD = 0.9999


def main() -> int:
    path = sys.argv[1] if len(sys.argv) > 1 else '/w/drift-sample.json'
    with open(path) as fh:
        rows = json.load(fh)
    if not rows:
        print('no sample rows -- nothing described yet?', file=sys.stderr)
        return 1

    from sentence_transformers import SentenceTransformer
    import numpy as np

    model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    texts = [r['text'] for r in rows]
    fresh = model.encode(texts)

    worst = 1.0
    failures = 0
    for row, new in zip(rows, fresh):
        # pgvector renders as '[0.1,0.2,...]'
        stored = np.array(json.loads(row['vector']), dtype=float)
        new = np.array(new, dtype=float)
        cos = float(stored @ new / (np.linalg.norm(stored) * np.linalg.norm(new)))
        worst = min(worst, cos)
        ok = cos >= THRESHOLD
        failures += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'DRIFT'} {cos:.6f}  {row['id']}  {row['text'][:56]!r}")

    print(f"\nworst cosine {worst:.6f} over {len(rows)} sampled descriptions (threshold {THRESHOLD})")
    if failures:
        print(f"FAIL: {failures} embedding(s) drifted -- the stored description index is no longer "
              f"in the same space as this image produces. Do not deploy.", file=sys.stderr)
        return 1
    print('pass: the embedding model is unchanged, the description index stays valid')
    return 0


if __name__ == '__main__':
    sys.exit(main())
