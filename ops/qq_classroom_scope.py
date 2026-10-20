"""Port the published resolver's final scope guard onto the older live overlay.

Use on a staged copy only; preserve the production module's other functions.
The shipping resolver already has this guard. This patches only legacy code.
"""
import ast


def patch_legacy_registry(source: str) -> str:
    """Filter exact aliases before they consume the search result limit."""
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'search_resources')
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Expr)
             and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
             and n.value.func.attr == 'append']
    anchor = min(calls, key=lambda n: n.lineno)
    if 'Exact aliases must obey' in source:
        raise ValueError('Registry already patched')
    guard = '''                # Exact aliases must obey the same scope as fuzzy matches.
                if resource_type and record.resource_type != resource_type:
                    continue
                if campus_code and record.location.get("campusCode", record.location.get("campus_code", "")) != campus_code:
                    continue
                if building_code and record.location.get("buildingCode", record.location.get("building_code", "")) != building_code:
                    continue
'''
    lines = source.splitlines(keepends=True)
    lines.insert(anchor.lineno - 1, guard)
    result = ''.join(lines)
    ast.parse(result)
    return result


def patch_legacy_resolver(source: str) -> str:
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'resolve_resource')
    anchor = next(n for n in function.body if isinstance(n, ast.Expr)
                  and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Attribute)
                  and n.value.func.attr == 'sort')
    if 'Text inference must never relax' in source:
        raise ValueError('Resolver already has a scope guard; inspect before applying')
    guard = '''    # Text inference must never relax explicit type or campus filters.
    candidates = [c for c in candidates if not resource_type or c.resource_type == resource_type]
    if campus_code:
        candidates = [c for c in candidates
                      if (record := get_resource(c.resource_id)) is not None
                      and record.location.get("campusCode", record.location.get("campus_code", "")) == campus_code]

'''
    lines = source.splitlines(keepends=True)
    lines.insert(anchor.lineno - 1, guard)
    result = ''.join(lines)
    ast.parse(result)
    return result
