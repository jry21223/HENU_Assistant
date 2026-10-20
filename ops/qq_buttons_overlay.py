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
