"""Merge the reviewed UI delta into independent live CMS changes, in memory.

Never read or write CMS credentials, cookies or source files. Callers provide
source strings; proofs contain only hashes and edit counts. Overlapping edits
fail closed rather than replacing independently changed control behavior.
"""
from dataclasses import dataclass
import difflib
import hashlib


class CmsMergeConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    text: str


def normalized(value):
    return value.replace('\r\n', '\n').strip() + '\n'


def edits(base, changed):
    old, new = base.splitlines(keepends=True), changed.splitlines(keepends=True)
    offsets = [0]
    for line in old:
        offsets.append(offsets[-1] + len(line))
    result = []
    for tag, a, b, c, d in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == 'equal':
            continue
        old_text, new_text = ''.join(old[a:b]), ''.join(new[c:d])
        if tag == 'replace' and len(old_text) + len(new_text) <= 30000:
            for subtag, i, j, k, l in difflib.SequenceMatcher(None, old_text, new_text, autojunk=False).get_opcodes():
                if subtag != 'equal':
                    result.append(Edit(offsets[a] + i, offsets[a] + j, new_text[k:l]))
        else:
            result.append(Edit(offsets[a], offsets[b], new_text))
    return result


def merge_reviewed_delta(base, reviewed, active):
    base, reviewed, active = map(normalized, (base, reviewed, active))
    ours, theirs = edits(base, reviewed), edits(base, active)
    combined = []
    for ours_edit in ours:
        for theirs_edit in theirs:
            if ours_edit == theirs_edit:
                break
            if ours_edit.start == ours_edit.end == theirs_edit.start == theirs_edit.end:
                continue  # Independent insertions share a stable boundary.
            overlap = max(ours_edit.start, theirs_edit.start) < min(ours_edit.end, theirs_edit.end)
            insertion_inside = (ours_edit.start == ours_edit.end and theirs_edit.start < ours_edit.start < theirs_edit.end) or (
                theirs_edit.start == theirs_edit.end and ours_edit.start < theirs_edit.start < ours_edit.end)
            if overlap or insertion_inside:
                raise CmsMergeConflict('Independent CMS edits overlap at base offsets '
                                       + str((ours_edit.start, ours_edit.end, theirs_edit.start, theirs_edit.end)))
        else:
            combined.append((0, ours_edit))
    combined.extend((1, edit) for edit in theirs)
    # New feature guards come before other inserted checks at the same exact
    # boundary; all independent existing code remains in its original order.
    combined.sort(key=lambda item: (item[1].start, item[1].end, item[0]))
    output, cursor = [], 0
    for _, edit in combined:
        if edit.start < cursor:
            raise CmsMergeConflict('Merged edit order is ambiguous')
        output.extend((base[cursor:edit.start], edit.text))
        cursor = edit.end
    output.append(base[cursor:])
    merged = ''.join(output)
    proof = {label + '_sha256': hashlib.sha256(value.encode()).hexdigest()
             for label, value in [('base', base), ('reviewed', reviewed), ('active', active), ('merged', merged)]}
    proof.update(reviewed_edits=len(ours), independent_edits_preserved=len(theirs), overlap_resolved_automatically=False)
    return merged, proof


VISIBILITY_WRAPPER = '''
    // Preserve the independently maintained live visibility policy.
    var mowerServicePreviousVisibility=pfVisibility;
    pfVisibility=function(s){var result=mowerServicePreviousVisibility(s);if(mowerServiceView(s).active){result.height=false;result.manualStart=false;result.manualPark=false;result.manualResume=false;result.husqvarnaStart=false;result.husqvarnaPark=false}return result};
'''


def merge_service_ui(base, reviewed, active):
    # The live template has a newer visibility policy. Preserve its function
    # exactly and apply the reviewed service restriction to its return value.
    guard = 'height:!mowerServiceView(s).active&&m.cuttingHeightSupported===true'
    if reviewed.count(guard) != 1:
        raise CmsMergeConflict('Reviewed visibility guard cannot be identified uniquely')
    adjusted = reviewed.replace(guard, 'height:m.cuttingHeightSupported===true', 1)
    merged, proof = merge_reviewed_delta(base, adjusted, active)
    anchor = '    if(!isPlatzwart(profileJSON))'
    if merged.count(anchor) != 1:
        raise CmsMergeConflict('Live UI installation anchor cannot be identified uniquely')
    merged = merged.replace(anchor, VISIBILITY_WRAPPER + anchor, 1)
    proof.update(live_visibility_policy_preserved=True,
                 delivered_sha256=hashlib.sha256(merged.encode()).hexdigest())
    return merged, proof
