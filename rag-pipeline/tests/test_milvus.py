#!/usr/bin/env python3
"""Minimal smoke-test: etcd + minio + Milvus working together.

Runs against the local Milvus instance (localhost:19530).
Start it first with:  bash runpod_start_local_milvus.sh

Tests:
  1. Connect
  2. Create a tiny collection (dim=4, IVF_FLAT/IP)
  3. Insert 5 known vectors
  4. Search — verify exact top-1 match
  5. Query by filter — verify row access
  6. Drop collection
"""

import math
import sys
import time

from pymilvus import DataType, MilvusClient

MILVUS_HOST = "localhost"
MILVUS_PORT = 19530
COLLECTION = "_smoke_test"
DIM = 4

OK_LABEL = "\033[32mPASS\033[0m"
FAIL_LABEL = "\033[31mFAIL\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:  # noqa: FBT001
    status = OK_LABEL if condition else FAIL_LABEL
    suffix = f"  ({detail})" if detail else ""
    print(f"  [{status}] {label}{suffix}")
    return condition


def normalise(v: list[float]) -> list[float]:
    mag = math.sqrt(sum(x * x for x in v))
    return [x / mag for x in v] if mag else v


def main() -> int:  # noqa: PLR0915
    ok = True

    # ── 1. Connect ────────────────────────────────────────────────────────────
    print("1. Connect")
    try:
        client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
        ok &= check("MilvusClient connected", True)  # noqa: FBT003
    except Exception as exc:  # noqa: BLE001
        check("MilvusClient connected", False, str(exc))  # noqa: FBT003
        return 1

    # Cleanup from a previous failed run
    if client.has_collection(COLLECTION):
        client.drop_collection(COLLECTION)

    # ── 2. Create collection ──────────────────────────────────────────────────
    print("2. Create collection (dim=4, IVF_FLAT/IP)")
    try:
        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("id", DataType.VARCHAR, max_length=16, is_primary=True)
        schema.add_field("label", DataType.VARCHAR, max_length=32)
        schema.add_field("vec", DataType.FLOAT_VECTOR, dim=DIM)

        ip = client.prepare_index_params()
        ip.add_index("vec", index_type="IVF_FLAT", metric_type="IP", params={"nlist": 4})

        client.create_collection(COLLECTION, schema=schema, index_params=ip)
        client.load_collection(COLLECTION)
        ok &= check("Collection created + loaded", client.has_collection(COLLECTION))
    except Exception as exc:  # noqa: BLE001
        ok &= check("Collection created + loaded", False, str(exc))  # noqa: FBT003
        return 1

    # ── 3. Insert ─────────────────────────────────────────────────────────────
    print("3. Insert 5 vectors")
    vectors = {
        "v1": normalise([1.0, 0.0, 0.0, 0.0]),
        "v2": normalise([0.0, 1.0, 0.0, 0.0]),
        "v3": normalise([0.0, 0.0, 1.0, 0.0]),
        "v4": normalise([0.0, 0.0, 0.0, 1.0]),
        "v5": normalise([1.0, 1.0, 0.0, 0.0]),
    }
    try:
        rows = [{"id": k, "label": k, "vec": v} for k, v in vectors.items()]
        _ = client.insert(COLLECTION, rows)
        ok &= check("Inserted 5 rows", True)  # noqa: FBT003
    except Exception as exc:  # noqa: BLE001
        ok &= check("Inserted 5 rows", False, str(exc))  # noqa: FBT003

    # Small delay so Milvus flushes the segment
    time.sleep(1)

    # ── 4. Search ─────────────────────────────────────────────────────────────
    print("4. Search nearest to v1=[1,0,0,0]")
    try:
        hits = client.search(
            COLLECTION,
            data=[normalise([1.0, 0.0, 0.0, 0.0])],
            anns_field="vec",
            search_params={"metric_type": "IP", "params": {"nprobe": 4}},
            limit=3,
            output_fields=["id", "label"],
        )
        top1_id = hits[0][0]["id"]
        top1_score = hits[0][0]["distance"]
        ok &= check("Top-1 is v1", top1_id == "v1", f"got {top1_id!r}")
        ok &= check("Score ≈ 1.0", abs(top1_score - 1.0) < 1e-4, f"score={top1_score:.4f}")
        ok &= check("Got 3 results", len(hits[0]) == 3, f"got {len(hits[0])}")
    except Exception as exc:  # noqa: BLE001
        ok &= check("Search", False, str(exc))  # noqa: FBT003

    # ── 5. Query by filter ────────────────────────────────────────────────────
    print('5. Query filter  id == "v3"')
    try:
        rows = list(client.query(COLLECTION, filter='id == "v3"', output_fields=["id", "label"]))
        ok &= check("Got 1 row", len(rows) == 1, f"got {len(rows)}")
        ok &= check("Row id == 'v3'", rows[0]["id"] == "v3", f"got {rows[0].get('id')!r}")
        ok &= check("label == 'v3'", rows[0]["label"] == "v3")
    except Exception as exc:  # noqa: BLE001
        ok &= check("Query by filter", False, str(exc))  # noqa: FBT003

    # ── 6. Drop ───────────────────────────────────────────────────────────────
    print("6. Drop collection")
    try:
        client.drop_collection(COLLECTION)
        ok &= check("Collection dropped", not client.has_collection(COLLECTION))
    except Exception as exc:  # noqa: BLE001
        ok &= check("Drop collection", False, str(exc))  # noqa: FBT003

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if ok:
        print(f"  {OK_LABEL}  All checks passed — Milvus stack (etcd + minio + milvus) is healthy.")
        return 0
    print(f"  {FAIL_LABEL}  Some checks failed — see output above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
