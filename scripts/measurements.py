"""Validate explicitly selected completed holds without hiding an interrupted run."""

def completed_levels(metadata):
    recorded=[stage['actors'] for stage in metadata['stages']]
    planned=metadata.get('workload',{}).get('levels',recorded)
    assert recorded, 'No completed measurement windows'
    if metadata['complete']:
        assert recorded==planned, 'Incomplete configured sweep'
    else:
        selected=metadata.get('accepted_completed_levels')
        assert selected and recorded==selected, 'Interrupted sweeps require an explicit completed-hold selection'
        assert planned[:len(selected)]==selected and len(selected)<len(planned), 'Selection must be the completed prefix of the planned sweep'
    return recorded
