def test_postgresql_transaction_rollback_leaves_no_test_row(
    postgres_transaction_connection,
):
    connection = postgres_transaction_connection
    owner_key = "postgres-rollback-integration-test"
    try:
        connection.execute("BEGIN")
        connection.execute(
            """
            INSERT INTO reading_goals (owner_key, weekly_target)
            VALUES (%s, %s)
            """,
            (owner_key, 3),
        )
        connection.rollback()
        row = connection.execute(
            "SELECT owner_key FROM reading_goals WHERE owner_key = %s",
            (owner_key,),
        ).fetchone()
        assert row is None
    finally:
        connection.rollback()
