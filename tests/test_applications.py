from core import applications


def test_classify_semiconductor_plasma():
    found = applications.classify("半导体刻蚀设备用 O 形圈，耐 plasma")
    assert ("semiconductor", "半导体") in found["industries"]
    assert ("plasma", "等离子") in found["media"]
    assert ("oring", "O形圈") in found["parts"]


def test_classify_english_boundary():
    # "gas" 不应命中 "gasket" 里的子串
    found = applications.classify("gasket for chemical pump")
    assert ("gas", "气体") not in found["media"]
    assert ("chemical", "化工") in found["industries"]


def test_enrich_keeps_llm_fields_and_packs():
    meta = applications.enrich(
        {
            "application": "汽车燃油系统密封",
            "industry": "汽车",
            "part": "密封件",
            "medium": "燃料",
            "certifications": "FDA / REACH",
            "supplier": "NOK",
        }
    )
    assert meta["industries"] == "汽车"
    assert meta["parts"] == "密封件"
    assert "燃料" in meta["media"]
    assert "FDA" in meta["certs"] and "REACH" in meta["certs"]
    assert meta["application_text"] == "汽车燃油系统密封"
    assert meta["supplier"] == "NOK"
    # 所有值都必须是 Chroma 可接受的标量
    assert all(isinstance(v, (str, int, float, bool)) for v in meta.values())


def test_enrich_infers_from_composition_and_medium():
    meta = applications.enrich({"composition": "全氟醚橡胶", "test_medium": "等离子"})
    assert "等离子" in meta["media"]
    assert "半导体" in meta["industries"]


def test_matches_by_label_or_code():
    meta = applications.enrich({"industry": "半导体", "medium": "酸"})
    assert applications.matches(meta, industry="半导体")
    assert applications.matches(meta, industry="semiconductor")
    assert applications.matches(meta, medium="酸")
    assert not applications.matches(meta, industry="汽车")


def test_matches_empty_means_any():
    assert applications.matches({}, "", "", "")
    assert applications.matches({"industries": ""}, "all")


def test_facets_counts():
    metas = [
        applications.enrich({"industry": "半导体", "medium": "酸"}),
        applications.enrich({"industry": "半导体", "medium": "溶剂"}),
        applications.enrich({"industry": "化工"}),
    ]
    facets = applications.facets(metas)
    counts = {item["label"]: item["count"] for item in facets["industries"]}
    assert counts["半导体"] == 2
    assert counts["化工"] == 1
