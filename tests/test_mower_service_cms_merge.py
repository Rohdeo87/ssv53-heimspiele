import pytest

from scripts.merge_mower_service_cms import CmsMergeConflict, merge_reviewed_delta


def test_independent_control_and_menu_changes_are_both_preserved():
    base = 'function control(){start(old);water(existing)}\nmenu("mower");\n'
    ours = 'function control(){if(winter)return;start(old);water(existing)}\nmenu("mower");menu("maintenance");\n'
    active = 'function control(){start(old);water(improved)}\nmenu("mower");menu("weather");\n'
    merged, proof = merge_reviewed_delta(base, ours, active)
    assert 'if(winter)return;start(old);water(improved)' in merged
    assert 'menu("maintenance")' in merged
    assert 'menu("weather")' in merged
    assert proof['independent_edits_preserved'] > 0


def test_conflicting_device_policy_is_never_overwritten():
    with pytest.raises(CmsMergeConflict):
        merge_reviewed_delta('start(10);\n', 'start(30);\n', 'start(90);\n')


def test_same_reviewed_delta_is_idempotent_and_normalizes_line_endings():
    merged, _ = merge_reviewed_delta('a\r\nb\r\n', 'a\nb\nc\n', 'a\r\nb\r\nc\r\n')
    assert merged == 'a\nb\nc\n'
