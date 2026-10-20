"""Port only the region metadata helper and summary method to a legacy module."""
import ast


def patch_tool_summaries(source: str, reference: str) -> str:
    """Keep production routing while porting the two presentation methods."""
    old, new = ast.parse(source), ast.parse(reference)
    old_cls = next(n for n in old.body if isinstance(n, ast.ClassDef) and n.name == 'HenuCliSafe')
    new_cls = next(n for n in new.body if isinstance(n, ast.ClassDef) and n.name == 'HenuCliSafe')
    names = {'_attach_semantic_options', '_format_locations_reply'}
    replacements = []
    ref_lines = reference.splitlines(keepends=True)
    for method in old_cls.body:
        if isinstance(method, ast.FunctionDef) and method.name in names:
            target = next(n for n in new_cls.body if isinstance(n, ast.FunctionDef) and n.name == method.name)
            replacements.append((method.lineno - 1, method.end_lineno, ref_lines[target.lineno - 1:target.end_lineno]))
    assert len(replacements) == 2
    lines = source.splitlines(keepends=True)
    for first, last, replacement in sorted(replacements, reverse=True):
        lines[first:last] = replacement
    result = ''.join(lines)
    ast.parse(result)
    return result


def patch_model_budget(source: str, reference: str) -> str:
    anchor = '        max_payload_chars = 2200\n'
    assert source.count(anchor) == 1 and reference.count(anchor) == 1
    extra = reference.split(anchor)[1].split('        payload = self._make_payload_json(result)\n')[0]
    assert 'max_payload_chars = 8000' in extra and 'max_payload_chars = 8000' not in source
    result = source.replace(anchor, anchor + extra, 1)
    ast.parse(result)
    return result


def patch_locations(source: str, reference: str) -> str:
    def span(node):
        return min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])]), node.end_lineno

    def method(tree):
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LocationMixin')
        return next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_location_summary')

    old, new = ast.parse(source), ast.parse(reference)
    if any(isinstance(n, ast.FunctionDef) and n.name == 'library_region_metadata' for n in old.body):
        raise ValueError('Metadata helper already exists; inspect before applying')
    helper = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == 'library_region_metadata')
    first, last = span(method(old))
    a, b = span(method(new))
    lines, reference_lines = source.splitlines(keepends=True), reference.splitlines(keepends=True)
    lines[first - 1:last] = reference_lines[a - 1:b]
    result = ''.join(lines).rstrip() + '\n\n\n' + ''.join(reference_lines[helper.lineno - 1:helper.end_lineno]) + '\n'
    ast.parse(result)
    return result
