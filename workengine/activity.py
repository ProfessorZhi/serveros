"""Activity DAG validation and readiness projection."""

from __future__ import annotations


class GraphError(ValueError):
    pass


class ActivityGraph:
    def __init__(self, dependencies: dict[str, list[str]]) -> None:
        self.dependencies = {node: tuple(needs) for node, needs in dependencies.items()}
        for node, needs in self.dependencies.items():
            missing = set(needs) - self.dependencies.keys()
            if missing:
                raise GraphError(f"activity {node} has missing dependencies: {sorted(missing)}")
        self._assert_acyclic()

    def _assert_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise GraphError(f"activity graph contains a cycle at {node}")
            if node in visited:
                return
            visiting.add(node)
            for dependency in self.dependencies[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for node in self.dependencies:
            visit(node)

    def ready(self, succeeded: set[str], finished: set[str] | None = None) -> list[str]:
        finished = set(finished or ()) | set(succeeded)
        return sorted(
            node
            for node, needs in self.dependencies.items()
            if node not in finished and set(needs).issubset(succeeded)
        )
