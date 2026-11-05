from __future__ import annotations

import pytest

from water_sample_ranker.matching.normalize import normalize_plast, normalize_well_id


@pytest.mark.parametrize(
    ("value", "expected_root", "expected_target"),
    [
        ("011", "11", "11"),
        (11, "11", "11"),
        (11.0, "11", "11"),
        ("11_ЗБС", "11", "11_ЗБС"),
        ("011_ЗБС 3", "11", "11_ЗБС3"),
        ("10501_збс(ГС1+ГС2)", "10501", "10501_ЗБС(ГС1+ГС2)"),
        ("10304_МЗС", "10304", "10304_МЗС"),
        ("54 ГС", "54", "54_ГС"),
    ],
)
def test_well_id_keeps_root_and_lifecycle_identity(
    value: object, expected_root: str, expected_target: str
) -> None:
    normalized = normalize_well_id(value)
    assert normalized.base_well_id == expected_root
    assert normalized.target_well_id == expected_target


def test_unknown_underscore_suffix_is_not_deleted() -> None:
    normalized = normalize_well_id("011_OTHER")
    assert normalized.base_well_id == "11"
    assert normalized.target_well_id == "11_OTHER"


def test_non_numeric_source_identifier_is_preserved() -> None:
    normalized = normalize_well_id("Р-183")
    assert normalized.base_well_id == normalized.target_well_id == "Р-183"


def test_mzs_zbs_and_pvlg_are_distinct_target_identities_with_shared_root() -> None:
    for root, variant in (("10403", "МЗС"), ("51", "ЗБС"), ("54", "ПВЛГ")):
        parent = normalize_well_id(root)
        child = normalize_well_id(f"0{root}_{variant}")
        assert parent.target_well_id != child.target_well_id
        assert child.base_well_id == parent.base_well_id
        assert child.target_well_id == f"{root}_{variant}"


def test_sequential_zbs_suffix_is_preserved_and_recognized() -> None:
    normalized = normalize_well_id("051_ЗБС2")

    assert normalized.base_well_id == "51"
    assert normalized.target_well_id == "51_ЗБС2"
    assert normalized.recognized_variant == "ЗБС"


@pytest.mark.parametrize("value", ["ПК₁", "ПК-1", "ПК 1"])
def test_plast_cosmetic_variants_match(value: str) -> None:
    assert normalize_plast(value) == "ПК1"


def test_plast_normalization_discards_sublayers_and_keeps_combinations() -> None:
    assert normalize_plast("ПК-19-2") == normalize_plast("ПК19(2)") == "ПК19"
    assert normalize_plast("ПК19-20") == "ПК19+20"
    assert normalize_plast("ПК19-2+20") == "ПК19+20"
    assert normalize_plast("ПК14-1, ПК15-1") == "ПК14+15"
    assert normalize_plast("ПК14+15") == "ПК14+15"
    assert normalize_plast("ПК14") != normalize_plast("ПК14+15")


def test_actual_lha_superscript_plast_is_supported() -> None:
    assert normalize_plast("ПК₁₉²") == "ПК19"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ПК17-2-1", "ПК17"),
        ("ПК14-1", "ПК14"),
        ("ПК19-20", "ПК19+20"),
        ("ПК14+15", "ПК14+15"),
        ("ПК14-1, ПК15-1", "ПК14+15"),
        (" ПК 15 – 1 + ПК14-2 ", "ПК14+15"),
    ],
)
def test_customer_plast_examples(raw: str, expected: str) -> None:
    assert normalize_plast(raw) == expected


@pytest.mark.parametrize(
    "raw,ordinal",
    [
        ("51_ЗБС", 1),
        ("51збс", 1),
        ("51-ЗБС", 1),
        ("51 ЗБС", 1),
        ("51_ЗБС1", 1),
        ("51_ЗБС2", 2),
        ("51збс2", 2),
        ("51 ЗБС 3", 3),
        ("51_ЗБС_МГРП", None),
        ("51збс_МГРП", None),
        ("51 ЗБС МГРП", None),
        ("51_ЗБС_ПИЛОТ2", None),
        ("51_ЗБС(ГС1+ГС2)", None),
    ],
)
def test_zbs_explicit_ordinal_and_annotated_family(raw: str, ordinal: int | None) -> None:
    parsed = normalize_well_id(raw)
    assert parsed.base_well_id == "51"
    assert parsed.recognized_variant == "ЗБС"
    assert parsed.ordinal == ordinal
    assert (parsed.annotation is not None) == (ordinal is None)
    if ordinal is None:
        assert parsed.target_well_id != "51_ЗБС"


@pytest.mark.parametrize(
    "root,family",
    [("112", "ЗБС"), ("10404", "ЗБС2"), ("10201", "ЗБС_МГРП"), ("54", "ГС"), ("54", "МЗС")],
)
def test_separator_variants_share_canonical_identity(root: str, family: str) -> None:
    parsed = [normalize_well_id(root + separator + family) for separator in ("_", " ", "", "-")]
    assert len({item.target_well_id for item in parsed}) == 1
    assert all(item.base_well_id == root for item in parsed)
    assert parsed[0].target_well_id != root
    assert parsed[0].separate_identity == (family in {"ГС", "МЗС"})


@pytest.mark.parametrize("raw", ["51ЗБСОМ", "51ГСАР", "51-МЗСВ", "51-22"])
def test_parser_does_not_consume_arbitrary_text(raw: str) -> None:
    assert normalize_well_id(raw).recognized_variant is None


def test_ordinal_branches_do_not_merge() -> None:
    assert normalize_well_id("51збс2").target_well_id != normalize_well_id("51збс").target_well_id


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ПК19-1+2", "ПК19"),
        ("ПК-19-1+2", "ПК19"),
        ("ПК19-2+1?", "ПК19"),
        ("ПК14-1+2", "ПК14"),
        ("ПК17-2-1", "ПК17"),
        ("ПК14-1", "ПК14"),
        ("ПК19-20", "ПК19+20"),
        ("ПК19-2+20", "ПК19+20"),
        ("ПК14+15", "ПК14+15"),
        ("ПК19/2+ПК20", "ПК19+20"),
        ("ПК19 + ПК20", "ПК19+20"),
    ],
)
def test_plast_grammar(raw: str, expected: str) -> None:
    assert normalize_plast(raw) == expected


def test_plast_shorthand_never_conflates_joint_primary() -> None:
    from water_sample_ranker.matching.normalize import parse_pc_plast

    parsed = parse_pc_plast("ПК19-1+2")
    assert parsed is not None
    assert len(parsed.components) == 1
    assert parsed.components[0].sublayers == (1, 2)
    assert normalize_plast("ПК19-1+2") != normalize_plast("ПК19+20")
    assert normalize_plast("ПК14") != normalize_plast("ПК14+15")


def test_explicit_primary_token_after_hyphen_is_joint_plast() -> None:
    assert normalize_plast("ПК19-ПК20") == "ПК19+20"
    assert normalize_plast("ПК₁₉²,ПК₂₀") == "ПК19+20"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ПК_1", "ПК1"),
        ("ПК20_2", "ПК20"),
        ("ПК20_0_1", "ПК20"),
        ("ПК19_1+2", "ПК19"),
        ("ПК19_2+1?", "ПК19"),
        ("ПК19-1+2", "ПК19"),
        ("ПК-19-1+2", "ПК19"),
        ("ПК17-2-1", "ПК17"),
        ("ПК14-1", "ПК14"),
        ("ПК19-20", "ПК19+20"),
        ("ПК19-2+20", "ПК19+20"),
        ("ПК14+15", "ПК14+15"),
        ("ПК19/2+ПК20", "ПК19+20"),
        ("ПК19-ПК20", "ПК19+20"),
        ("ПК _ 1", "ПК1"),
        ("OTHER_1", "OTHER_1"),
        ("OTHER _ 1", "OTHER _ 1"),
        ("АТ_1", "АТ_1"),
        ("10302_ЗБС", "10302_ЗБС"),
        ("10201_ЗБС_МГРП", "10201_ЗБС_МГРП"),
    ],
)
def test_underscore_pc_grammar_and_preserved_separators(raw: str, expected: str) -> None:
    assert normalize_plast(raw) == expected


def test_underscore_does_not_conflate_primary_combinations_or_well_identity() -> None:
    assert normalize_plast("ПК19_1+2") != normalize_plast("ПК19+20")
    assert normalize_plast("ПК14") != normalize_plast("ПК14+15")
    assert normalize_well_id("10201_ЗБС_МГРП").target_well_id == "10201_ЗБС_МГРП"
