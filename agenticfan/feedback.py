"""Fresh hardware observations, scoped to one Control instance/session."""
from .curves import finite_number

MAX_AGE = 3.0


def expire(state, now, active=True):
    """Recheck freshness when reading a persisted runtime snapshot as well."""
    state = dict(state)
    sampled = state.get('sampled_monotonic')
    age = now - sampled if finite_number(sampled) else None
    state['age_seconds'] = age
    if not active:
        state['status'] = 'stopped'
        state['pending_update'] = False
    elif sampled is not None and (age is None or not 0 <= age <= MAX_AGE):
        state['status'] = 'stale'
    return state


class Feedback:
    def __init__(self, fan):
        self.fan = str(fan)
        self.latest = None

    def observe(self, data, requested_sequence, requested_rpm, now):
        sequence, target = data.get('sequence'), data.get('target_rpm')
        sampled, actuals = data.get('sampled_monotonic'), data.get('actual_rpm')
        if (type(sequence) is not int or not 0 <= sequence <= requested_sequence
                or type(target) is not int or target <= 0
                or (sequence == requested_sequence and target != requested_rpm)
                or not finite_number(sampled) or sampled > now
                or not isinstance(data.get('sampled_at'), str)
                or not isinstance(actuals, dict) or set(actuals) != {self.fan}):
            return
        actual = actuals[self.fan]
        if not finite_number(actual) or actual < 0:
            return
        if self.latest and (sampled <= self.latest['sampled_monotonic']
                            or sequence < self.latest['sequence']):
            return
        tolerance = max(100, target * .08)
        self.latest = {'sequence': sequence, 'target_rpm': target, 'actual_rpm': actual,
                       'sampled_monotonic': sampled, 'sampled_at': data['sampled_at'],
                       'tolerance_rpm': tolerance,
                       'within_tolerance': abs(actual - target) <= tolerance}

    def snapshot(self, requested_sequence, now, active=True):
        state = {'status': 'waiting', 'pending_update': True}
        if self.latest:
            state.update(self.latest)
            state['status'] = 'tracking' if self.latest['within_tolerance'] else 'adjusting'
            state['pending_update'] = self.latest['sequence'] != requested_sequence
        return expire(state, now, active)
