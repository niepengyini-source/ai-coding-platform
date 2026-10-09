"""Use the project's explicit code hierarchy, never guess from code names."""


def top_level_ids(codes):
    parents = {code.id: code.parent_id for code in codes}
    roots = {}
    for code_id in parents:
        current, visited = code_id, set()
        while current in parents and parents[current] is not None:
            if current in visited:
                current = None
                break
            visited.add(current)
            current = parents[current]
        roots[code_id] = current if current in parents else None
    return roots
