"""
Run this once to set up Neo4j constraints and indexes.
Usage: python packages/graph-schema/init_schema.py
"""

from __future__ import annotations

import os
from pathlib import Path

from neo4j import GraphDatabase

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "rabbitholepass")


def main() -> None:
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    cypher_file = Path(__file__).parent / "constraints.cypher"
    statements = [
        s.strip()
        for s in cypher_file.read_text().split(";")
        if s.strip() and not s.strip().startswith("//")
    ]
    with driver.session() as session:
        for stmt in statements:
            if stmt:
                try:
                    session.run(stmt)
                    print(f"✓ {stmt[:60]}...")
                except Exception as exc:
                    print(f"✗ {stmt[:60]}... — {exc}")
    driver.close()
    print("Schema initialization complete.")


if __name__ == "__main__":
    main()
