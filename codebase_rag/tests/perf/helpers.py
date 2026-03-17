from __future__ import annotations

from pathlib import Path

from codebase_rag.tests.integration.semantic_fixtures.helpers import (
    SemanticFixtureSpec,
    materialize_fixture_repo,
)


def materialize_semantic_stress_repo(base_dir: Path) -> Path:
    sql_lines = "\n".join(
        f'    session.execute("SELECT * FROM invoices WHERE id = {index}")'
        for index in range(48)
    )
    cypher_lines = "\n".join(
        f'    graph.run("MATCH (n:Invoice) WHERE n.id = {index} RETURN n")'
        for index in range(48)
    )
    env_lines = "\n".join(
        f'    values.append(os.getenv("FEATURE_STRESS_{index}"))' for index in range(60)
    )
    event_lines = "\n".join(
        f'    publisher.publish("invoice.created.{index}", {{"id": "{index}"}}, queue="invoice-events-{index}")'
        for index in range(36)
    )
    side_effect_lines = "\n".join(
        [f'    db.insert({{"id": "{index}"}})' for index in range(16)]
        + [f'    cache.set("invoice:{index}", "cached")' for index in range(12)]
        + ['    post("https://example.com/hooks")' for _ in range(12)]
    )
    dotenv_lines = "\n".join(
        [f"FEATURE_STRESS_{index}=1" for index in range(60)]
        + [f"APP_SECRET_{index}=secret-value-{index:03d}" for index in range(12)]
    )

    spec = SemanticFixtureSpec(
        name="semantic_guardrail_stress_fixture",
        files={
            ".env": f"{dotenv_lines}\n",
            "queries.py": f"""def run_sql_queries(session) -> None:
{sql_lines}


def run_cypher_queries(graph) -> None:
{cypher_lines}
""",
            "settings.py": f"""import os


def read_many_envs() -> list[str | None]:
    values: list[str | None] = []
{env_lines}
    return values
""",
            "events.py": f"""class Publisher:
    def publish(self, event: str, payload: dict[str, object], queue: str) -> None:
        return None


publisher = Publisher()


def publish_many() -> None:
{event_lines}
""",
            "transactions.py": f"""from requests import post


class Session:
    def begin(self):
        return self

    def commit(self):
        return None


class Cache:
    def set(self, key: str, value: str) -> None:
        return None


session = Session()
cache = Cache()


def persist_many(db) -> None:
    tx = session.begin()
{side_effect_lines}
    tx.commit()
""",
        },
    )
    return materialize_fixture_repo(base_dir, spec)
