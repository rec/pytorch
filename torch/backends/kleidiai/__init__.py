# mypy: allow-untyped-defs
import torch


def is_available() -> bool:
    r"""Return whether PyTorch is built with KleidiAI support."""
    return torch._C._has_kleidiai
