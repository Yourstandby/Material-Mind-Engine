import pytest

from core import catalog


def test_material_ffkm():
    assert catalog.material_of("四氟乙烯与全氟烷基乙烯基醚共聚物") == "FFKM"
    assert catalog.material_of("Perfluoroelastomer") == "FFKM"


def test_material_peek():
    assert catalog.material_of("聚醚醚酮") == "PEEK"


def test_material_unknown():
    assert catalog.material_of("something else", "") == "OTHER"


def test_supplier_drops_domain():
    assert catalog.clean_supplier("www.teflus.com") == ""
    assert catalog.clean_supplier("https://example.com") == ""
    assert catalog.clean_supplier("Greene Tweed") == "Greene Tweed"


def test_supplier_drops_material_code():
    assert catalog.clean_supplier("FFKM") == ""


def test_product_name_removes_duplicate_prefix():
    assert catalog.product_name("Perlast", "Perlast® G75M") == "Perlast G75M"


def test_product_key_stable_across_trademark():
    a = catalog.product_key("Greene Tweed", "CHEMRAZ® 640")
    b = catalog.product_key("Greene Tweed", "CHEMRAZ 640")
    assert a == b


def test_product_name_fallback():
    assert catalog.product_name("", "") == "未标注产品"
