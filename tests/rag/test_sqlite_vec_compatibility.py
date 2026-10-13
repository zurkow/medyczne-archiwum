import sqlite3

import sqlite_vec


def test_vec0_supports_per_user_partitioned_knn():
    connection = sqlite3.connect(":memory:")
    connection.enable_load_extension(True)
    sqlite_vec.load(connection)
    connection.execute(
        "CREATE VIRTUAL TABLE chunks USING vec0("
        "chunk_id TEXT PRIMARY KEY, owner_partition TEXT partition key, "
        "embedding float[3] distance_metric=cosine)"
    )
    connection.execute(
        "INSERT INTO chunks(chunk_id, owner_partition, embedding) VALUES "
        "('own', 'owner-a', '[1, 0, 0]'), ('other', 'owner-b', '[1, 0, 0]')"
    )

    rows = connection.execute(
        "SELECT chunk_id FROM chunks WHERE embedding MATCH ? AND k = 10 AND owner_partition = ?",
        ("[1, 0, 0]", "owner-a"),
    ).fetchall()
    assert rows == [("own",)]
