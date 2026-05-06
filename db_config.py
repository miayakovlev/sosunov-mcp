import os


def get_postgres_dsn() -> str:
    host = os.getenv("POSTGRES_HOST", "127.0.0.1")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "myday")
    user = os.getenv("POSTGRES_USER", "myday")
    password = os.getenv("POSTGRES_PASSWORD", "myday")
    sslmode = os.getenv("POSTGRES_SSLMODE", "prefer")

    return (
        f"host={host} "
        f"port={port} "
        f"dbname={database} "
        f"user={user} "
        f"password={password} "
        f"sslmode={sslmode}"
    )
