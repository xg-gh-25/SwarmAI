"""structured() decodes a list or object the model sent as JSON text (2026-10-29-cdf-launch
§1 A1). Every failing CDF extraction had a straight double quote inside a text field,
written unescaped; the strict type check then refused the whole answer."""
import pytest

from jobs import bedrock as jb

LIST = {"type": "object", "properties": {"units": {"type": "array", "items": {
    "type": "object", "properties": {"what": {"type": "string"}, "why": {"type": "string"}},
    "required": ["what"]}}}, "required": ["units"]}


def test_valid_json_text_is_decoded():
    out = jb._validate({"units": '[{"what": "a", "why": "b"}]'}, LIST)
    assert out == {"units": [{"what": "a", "why": "b"}]}


def test_unescaped_inner_quotes_are_repaired():
    raw = '[{"what": "旧记录不改", "why": "遵循"不碰历史数据"的规则"}, {"what": "x: "y" z"}]'
    out = jb._validate({"units": raw}, LIST)
    assert out["units"] == [{"what": "旧记录不改", "why": '遵循"不碰历史数据"的规则'},
                            {"what": 'x: "y" z'}]


def test_text_that_is_not_the_expected_kind_is_still_refused():
    with pytest.raises(ValueError):
        jb._validate({"units": '{"what": "an object, not a list"}'}, LIST)
    with pytest.raises(ValueError):
        jb._validate({"units": "plain words"}, LIST)


def test_a_decoded_list_is_still_validated():
    with pytest.raises(ValueError):
        jb._validate({"units": '[{"why": "no what"}]'}, LIST)


def test_escaped_quotes_are_left_alone():
    assert jb._repair_inner_quotes('{"a": "say \\"hi\\""}') == '{"a": "say \\"hi\\""}'
