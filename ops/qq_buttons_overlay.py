"""Small, verifiable additions to the already-overlaid QQ adapter."""

import ast


def patch_host(source):
    additions = {
        "reply_message": "from .henu_qq_cards import reply_synthetic_image\nif await reply_synthetic_image(self, message_source, message):\n    return\n",
        "_handle_interaction_create": "from .henu_qq_cards import handle_click\nif await handle_click(self, event_data, ws_event_id):\n    return\n",
    }
    lines = source.splitlines(keepends=True)
    inserts = []
    for name, addition in additions.items():
        if addition.splitlines()[0] in source:
            continue
        candidates = [
            n
            for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.AsyncFunctionDef) and n.name == name
        ]
        if len(candidates) != 1:
            raise ValueError("ambiguous QQ adapter method: " + name)
        node = candidates[0]
        body = node.body
        first = (
            body[1]
            if isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
            else body[0]
        )
        indent = " " * first.col_offset
        inserts.append(
            (
                first.lineno - 1,
                "".join(indent + line + "\n" for line in addition.splitlines()),
            )
        )
    for index, text in sorted(inserts, reverse=True):
        lines.insert(index, text)
    result = "".join(lines)
    ast.parse(result)
    return result


def patch_tool(source):
    """Retain all production business overrides; decorate only the tool call."""
    if "@confirmation_context" in source:
        return source
    tree = ast.parse(source)
    classes = [
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HenuCliSafe"
    ]
    if len(classes) != 1:
        raise ValueError("ambiguous safe CLI class")
    methods = [
        n
        for n in classes[0].body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "call"
    ]
    if len(methods) != 1:
        raise ValueError("ambiguous safe CLI call")
    node = methods[0]
    lines = source.splitlines(keepends=True)
    lines.insert(
        min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1,
        " " * node.col_offset + "@confirmation_context\n",
    )
    # This module already imports sibling henu_plugin modules before its class.
    index = classes[0].lineno - 1
    lines.insert(index, "from henu_plugin.qq_buttons import confirmation_context\n\n")
    result = "".join(lines)
    ast.parse(result)
    return result
