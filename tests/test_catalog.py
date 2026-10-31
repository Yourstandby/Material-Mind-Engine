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


def test_supplier_is_not_product():
    """只有厂商没有牌号时，产品名不能退回厂商名。"""
    from core import catalog

    assert catalog.product_name("Solvay", "") == catalog.UNNAMED
    assert catalog.product_name("3M", "") == catalog.UNNAMED
    assert catalog.product_name("Solvay", "Varox DBPH-50") == "Solvay Varox DBPH-50"


def test_product_title_ignores_stored_supplier_name():
    """老数据 product_name == supplier 时按未标注处理，回填后自愈。"""
    from core import catalog

    assert catalog.product_title("Solvay", "", "Solvay") == catalog.UNNAMED
    assert catalog.product_title("Chemours", "", "Chemours") == catalog.UNNAMED
    assert catalog.product_title("Solvay", "XPE", "") == "Solvay XPE"
    assert catalog.product_title("", "", "S-FFKM-1") == "S-FFKM-1"
