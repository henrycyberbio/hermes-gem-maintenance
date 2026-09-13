"""Parsing gene-reaction rules into stable, comparable boolean logic."""

from __future__ import annotations

import ast

from cobra.core.gene import GPR


class UncomparableGPRError(ValueError):
    """A gene rule cannot be reduced to the boolean logic this package compares."""


def canonical_gpr(rule: str) -> object:
    """Return a stable boolean form, or refuse a rule that cannot be reduced."""
    if not rule:
        return ""

    def walk(node: ast.AST) -> object:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.BoolOp):
            operator = "and" if isinstance(node.op, ast.And) else "or"
            operands: list[object] = []
            for value in node.values:
                child = walk(value)
                if isinstance(child, tuple) and child[0] == operator:
                    operands.extend(child[1])
                else:
                    operands.append(child)
            return (operator, tuple(sorted(operands, key=str)))
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Expr):
            return walk(node.value)
        if isinstance(node, ast.Module):
            body = node.body
            return walk(body[0] if isinstance(body, list) else body)
        raise UncomparableGPRError(ast.dump(node))

    try:
        parsed = GPR.from_string(rule)
    except (SyntaxError, TypeError, ValueError) as exc:
        raise UncomparableGPRError(rule) from exc
    if not str(parsed).strip():
        raise UncomparableGPRError(rule)
    try:
        return walk(parsed)
    except (TypeError, ValueError) as exc:
        raise UncomparableGPRError(rule) from exc


def comparable_gpr(rule: str) -> object:
    """Return canonical logic or a rule-specific marker for snapshot comparison."""
    try:
        return canonical_gpr(rule)
    except UncomparableGPRError:
        return ("unreducible", rule)
