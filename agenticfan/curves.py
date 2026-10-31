"""Deterministic temperature/RPM interpolation and time-based slew limiting."""
from copy import deepcopy
import math

from .errors import AgenticFanError, invalid

DEFAULT_RAMP = 200


def finite_number(value):
    try:
        return type(value) in (float, int) and math.isfinite(value)
    except OverflowError:
        return False


def validate(curve, groups):
    fields = {"sensor_group", "nodes"}
    if not isinstance(curve, dict) or not fields <= set(curve) or set(curve) - fields - {"ramp_rpm_per_second"}:
        raise invalid("A curve requires sensor_group and nodes, with optional ramp_rpm_per_second.")
    if not isinstance(curve["sensor_group"], str) or curve["sensor_group"] not in groups:
        raise invalid("Choose an identified temperature group for the curve.")
    rate = curve.get("ramp_rpm_per_second", DEFAULT_RAMP)
    if not finite_number(rate) or not 1 <= rate <= 1000:
        raise invalid("Curve ramp_rpm_per_second must be finite and between 1 and 1000.")
    nodes = curve["nodes"]
    if not isinstance(nodes, list) or not 2 <= len(nodes) <= 16:
        raise invalid("A curve needs 2 through 16 temperature/RPM nodes.")
    previous_temperature, previous_rpm = -math.inf, 0
    for node in nodes:
        if not isinstance(node, dict) or set(node) != {"temperature_celsius", "target_rpm"}:
            raise invalid("Each curve node requires only temperature_celsius and target_rpm.")
        temperature, rpm = node["temperature_celsius"], node["target_rpm"]
        if not finite_number(temperature) or not -40 <= temperature <= 150 or temperature <= previous_temperature:
            raise invalid("Curve temperatures must be finite, strictly increasing and within -40..150 C. These are plausibility bounds, not safe operating limits.")
        if type(rpm) is not int or not 1 <= rpm <= 16383 or rpm < previous_rpm:
            raise invalid("Curve RPM values must be positive integers, nondecreasing and within 1..16383.")
        previous_temperature, previous_rpm = temperature, rpm
    return deepcopy(curve)


def validate_fan(curve, fan):
    if (not fan or not fan.get("control_available") or not finite_number(fan.get("min_rpm"))
            or not finite_number(fan.get("max_rpm")) or not 0 < fan["min_rpm"] < fan["max_rpm"]):
        raise AgenticFanError("RULE_FAN_UNAVAILABLE", "Selected fan has no usable control interface or RPM bounds.")
    if any(not fan["min_rpm"] <= node["target_rpm"] <= fan["max_rpm"] for node in curve["nodes"]):
        raise invalid("Every curve node must fit the selected fan's reported RPM bounds.",
                      min_rpm=fan["min_rpm"], max_rpm=fan["max_rpm"])


def interpolate(nodes, temperature):
    if temperature <= nodes[0]["temperature_celsius"]:
        return nodes[0]["target_rpm"]
    for left, right in zip(nodes, nodes[1:]):
        if temperature <= right["temperature_celsius"]:
            fraction = ((temperature - left["temperature_celsius"])
                        / (right["temperature_celsius"] - left["temperature_celsius"]))
            return round(left["target_rpm"] + fraction * (right["target_rpm"] - left["target_rpm"]))
    return nodes[-1]["target_rpm"]


class Slew:
    def __init__(self, value, now):
        self.value, self.now = value, now

    def step(self, target, now, rate):
        # A delayed tick must never turn into a large catch-up jump.
        elapsed = max(0, min(2, now - self.now))
        self.now = now
        delta = target - self.value
        self.value += math.copysign(min(abs(delta), rate * elapsed), delta)
        return round(self.value)
