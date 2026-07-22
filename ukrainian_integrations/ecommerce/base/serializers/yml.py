from __future__ import annotations

from .xml import XMLSerializer


class YMLSerializer(XMLSerializer):
    """YML catalog files are XML documents with a provider-specific layout."""

    format = "YML"
