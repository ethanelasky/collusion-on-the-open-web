"""Display labels are derived from recorded settings without changing evidence."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from ai_collusion.docent_presentation import display_label, presentation


@pytest.mark.parametrize('arm,mode,p,label', [
    ('harder-v3', 'neutral', None, 'slow-data'),
    ('slow-data-v4', 'neutral', None, 'slow-data'),
    ('unreliable-v3', 'neutral', 0.75, 'unreliable-75%'),
    ('unreliable', 'neutral', 0.5, 'unreliable-50%'),
    ('unreliable', 'neutral', 0, 'unreliable-0%'),
    ('unreliable', 'neutral', None, 'unreliable'),
    ('legacy', 'evil', None, 'failing'),
    ('broken-v3', 'evil', None, 'broken'),
])
def test_labels_follow_recorded_config(arm, mode, p, label):
    metadata = {'model': 'test', 'arm_id': arm, 'episode': {'mode': mode},
                'resolved_config': {'cut': {'query_failure_probability': p}}}
    run = SimpleNamespace(metadata=metadata, transcripts=[])
    before = deepcopy(metadata)
    title, fields, tags = presentation(run)
    assert label in title and fields['environment'] == label
    assert not any('harder' in tag or 'evil' in tag for tag in tags)
    assert metadata == before
    if p is not None:
        assert label in fields['arm_config']


def test_display_label_is_idempotent_and_handles_preview_condition_names():
    text = 'evaluee | Task@4:sparse_harder_v3:env-evil:arm-harder-v3'
    fixed = display_label(text)
    assert 'harder' not in fixed and 'evil' not in fixed
    assert display_label(fixed) == fixed
    fixed = display_label('unreliable-v4', {'query_failure_probability': 0.75})
    assert fixed == 'unreliable-75%-v4'
    assert display_label(fixed, {'query_failure_probability': 0.75}) == fixed
    assert display_label('unreliable-v4') == 'unreliable-v4'  # never guess an absent rate
