# validate extraction JSON against the graphify schema before graph assembly
from __future__ import annotations

VALID_FILE_TYPES = {"code", "document", "paper", "image", "rationale", "concept"}
VALID_CONFIDENCES = {"EXTRACTED", "INFERRED", "AMBIGUOUS"}
REQUIRED_NODE_FIELDS = {"id", "label", "file_type", "source_file"}
REQUIRED_EDGE_FIELDS = {"source", "target", "relation", "confidence", "source_file"}

# Stage 3 — sleep-cycle field constants.
# VALID_ORIGINS: extended from Stage 2 ("private"|"shared") to include cycle-produced origins.
VALID_ORIGINS = {"private", "shared", "replay", "rem_dream", "hypothesis"}

# VALID_RELATIONS: informational constant — lists known relation types including the new
# "grounded_in" edge (Hypothesis node → Source node).  NOT enforced by validate_extraction
# because the open-world set of LLM-generated relations cannot be exhaustively listed.
VALID_RELATIONS = {
    "calls", "imports", "uses", "defines", "implements", "extends",
    "references", "documents", "tests", "depends_on", "contains",
    "relates_to", "grounded_in",
}

# Weight mapping by confidence level — used by build.py when defaulting edge weight.
CONFIDENCE_WEIGHT_DEFAULTS: dict[str, float] = {
    "EXTRACTED": 1.0,
    "INFERRED": 0.6,
    "AMBIGUOUS": 0.3,
}


def validate_extraction(data: dict) -> list[str]:
    """
    Validate an extraction JSON dict against the graphify schema.
    Returns a list of error strings - empty list means valid.
    """
    if not isinstance(data, dict):
        return ["Extraction must be a JSON object"]

    errors: list[str] = []

    # Nodes
    if "nodes" not in data:
        errors.append("Missing required key 'nodes'")
    elif not isinstance(data["nodes"], list):
        errors.append("'nodes' must be a list")
    else:
        for i, node in enumerate(data["nodes"]):
            if not isinstance(node, dict):
                errors.append(f"Node {i} must be an object")
                continue
            for field in REQUIRED_NODE_FIELDS:
                if field not in node:
                    errors.append(f"Node {i} (id={node.get('id', '?')!r}) missing required field '{field}'")
            if "file_type" in node and node["file_type"] not in VALID_FILE_TYPES:
                errors.append(
                    f"Node {i} (id={node.get('id', '?')!r}) has invalid file_type "
                    f"'{node['file_type']}' - must be one of {sorted(VALID_FILE_TYPES)}"
                )
            # Stage 3 optional fields: validate when present, accept when absent.
            if "weight" in node:
                w = node["weight"]
                if not isinstance(w, (int, float)) or not (0.0 <= float(w) <= 1.0):
                    errors.append(
                        f"Node {i} (id={node.get('id', '?')!r}) weight {w!r} "
                        f"out of range [0.0, 1.0]"
                    )
            if "uses" in node and (not isinstance(node["uses"], int) or node["uses"] < 0):
                errors.append(
                    f"Node {i} (id={node.get('id', '?')!r}) 'uses' must be a non-negative int"
                )
            if "max_observed_degree" in node and (
                not isinstance(node["max_observed_degree"], int)
                or node["max_observed_degree"] < 0
            ):
                errors.append(
                    f"Node {i} (id={node.get('id', '?')!r}) "
                    f"'max_observed_degree' must be a non-negative int"
                )
            if "origin" in node and node["origin"] not in VALID_ORIGINS:
                errors.append(
                    f"Node {i} (id={node.get('id', '?')!r}) origin {node['origin']!r} "
                    f"not in {sorted(VALID_ORIGINS)}"
                )

    # Edges - accept "links" (NetworkX <= 3.1) as fallback for "edges"
    edge_list = data.get("edges") if "edges" in data else data.get("links")
    if edge_list is None:
        errors.append("Missing required key 'edges'")
    elif not isinstance(edge_list, list):
        errors.append("'edges' must be a list")
    else:
        node_ids = {n["id"] for n in data.get("nodes", []) if isinstance(n, dict) and "id" in n}
        for i, edge in enumerate(edge_list):
            if not isinstance(edge, dict):
                errors.append(f"Edge {i} must be an object")
                continue
            for field in REQUIRED_EDGE_FIELDS:
                if field not in edge:
                    errors.append(f"Edge {i} missing required field '{field}'")
            if "confidence" in edge and edge["confidence"] not in VALID_CONFIDENCES:
                errors.append(
                    f"Edge {i} has invalid confidence '{edge['confidence']}' "
                    f"- must be one of {sorted(VALID_CONFIDENCES)}"
                )
            if "source" in edge and node_ids and edge["source"] not in node_ids:
                errors.append(f"Edge {i} source '{edge['source']}' does not match any node id")
            if "target" in edge and node_ids and edge["target"] not in node_ids:
                errors.append(f"Edge {i} target '{edge['target']}' does not match any node id")
            # Stage 3 optional edge fields: validate when present, accept when absent.
            if "weight" in edge:
                w = edge["weight"]
                if not isinstance(w, (int, float)) or not (0.0 <= float(w) <= 1.0):
                    errors.append(
                        f"Edge {i} weight {w!r} out of range [0.0, 1.0]"
                    )
            if "uses" in edge and (not isinstance(edge["uses"], int) or edge["uses"] < 0):
                errors.append(f"Edge {i} 'uses' must be a non-negative int")
            if "origin" in edge and edge["origin"] not in VALID_ORIGINS:
                errors.append(
                    f"Edge {i} origin {edge['origin']!r} not in {sorted(VALID_ORIGINS)}"
                )

    return errors


def assert_valid(data: dict) -> None:
    """Raise ValueError with all errors if extraction is invalid."""
    errors = validate_extraction(data)
    if errors:
        msg = f"Extraction JSON has {len(errors)} error(s):\n" + "\n".join(f"  • {e}" for e in errors)
        raise ValueError(msg)
