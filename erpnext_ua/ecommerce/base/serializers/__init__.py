from __future__ import annotations

from .csv import CSVSerializer
from .transforms import registered_custom_transforms
from .xml import XMLSerializer
from .yml import YMLSerializer


def get_serializer(file_format: str):
    serializers = {
        "CSV": CSVSerializer,
        "XML": XMLSerializer,
        "YML": YMLSerializer,
    }
    try:
        return serializers[str(file_format or "").strip().upper()]()
    except KeyError as exc:
        raise ValueError(f"Unsupported ecommerce file format: {file_format}") from exc


__all__ = [
    "CSVSerializer",
    "XMLSerializer",
    "YMLSerializer",
    "get_serializer",
    "registered_custom_transforms",
]
