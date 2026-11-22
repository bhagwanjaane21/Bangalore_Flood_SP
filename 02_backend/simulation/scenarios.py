"""Illustrative sensor sequences, not calibrated rainfall or hydraulic models."""

from dataclasses import dataclass, field

NODES = ("SENSOR_01", "SENSOR_02", "SENSOR_03")
BASE_DEPTHS = dict(zip(NODES, (0.20, 0.25, 0.15)))
GREEN = dict.fromkeys(NODES, "GREEN")


@dataclass(frozen=True)
class Step:
    offset_seconds: int
    depths: dict[str, float]
    expected_levels: dict[str, str]
    label: str
    expected_kinds: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    steps: tuple[Step, ...]
    transitions: tuple[tuple[str, str, str], ...] = ()
    dropout_node: str | None = None


def step(offset, depths=None, levels=None, label="", kinds=None):
    return Step(offset, BASE_DEPTHS | (depths or {}), GREEN | (levels or {}),
                label, kinds or {})


SCENARIOS = {
    "normal": Scenario("normal", "Stable readings; no alert transitions.", tuple(
        step(second, label="Stable baseline") for second in (0, 30, 60, 90)
    )),
    "sustained-rise": Scenario(
        "sustained-rise", "Repeated upstream rises trigger trend warnings below depth thresholds.",
        tuple(step(index * 30, {"SENSOR_01": depth}, {"SENSOR_01": level},
                   "Upstream trend", {"SENSOR_01": "trend"} if index >= 2 else {})
              for index, (depth, level) in enumerate(zip(
                  (.20, .21, .22, .245, .27, .295),
                  ("GREEN", "GREEN", "YELLOW", "YELLOW", "YELLOW", "ORANGE")))),
        (("SENSOR_01", "YELLOW", "escalation"), ("SENSOR_01", "ORANGE", "escalation")),
    ),
    "progressing-surge": Scenario(
        "progressing-surge", "Illustrative upstream-to-downstream sequence; travel times are not forecasts.",
        (
            step(0, label="All nodes at baseline"),
            step(30, {"SENSOR_01": .85}, {"SENSOR_01": "YELLOW"}, "Upstream advisory"),
            step(60, {"SENSOR_01": 1.25, "SENSOR_02": .75},
                 {"SENSOR_01": "ORANGE", "SENSOR_02": "YELLOW"}, "Midstream begins rising"),
            step(90, {"SENSOR_01": 1.65, "SENSOR_02": 1.25, "SENSOR_03": .9},
                 {"SENSOR_01": "RED", "SENSOR_02": "ORANGE", "SENSOR_03": "YELLOW"}, "Downstream advisory"),
            step(120, {"SENSOR_01": 1.65, "SENSOR_02": 1.7, "SENSOR_03": 1.3},
                 {"SENSOR_01": "RED", "SENSOR_02": "RED", "SENSOR_03": "ORANGE"}, "Gate 4 warning"),
            step(150, {"SENSOR_01": 1.65, "SENSOR_02": 1.7, "SENSOR_03": 1.65},
                 dict.fromkeys(NODES, "RED"), "Immediate local critical depth"),
        ),
        (("SENSOR_01", "YELLOW", "escalation"),
         ("SENSOR_01", "ORANGE", "escalation"), ("SENSOR_02", "YELLOW", "escalation"),
         ("SENSOR_01", "RED", "escalation"), ("SENSOR_02", "ORANGE", "escalation"),
         ("SENSOR_03", "YELLOW", "escalation"), ("SENSOR_02", "RED", "escalation"),
         ("SENSOR_03", "ORANGE", "escalation"), ("SENSOR_03", "RED", "escalation")),
    ),
    "recovery": Scenario(
        "recovery", "One low observation holds RED; a second confirms recovery.",
        (step(0, label="Baseline"),
         step(30, {"SENSOR_03": 1.65}, {"SENSOR_03": "RED"}, "Local critical depth"),
         step(60, levels={"SENSOR_03": "RED"}, label="First recovery observation",
              kinds={"SENSOR_03": "held"}),
         step(90, label="Recovery confirmed", kinds={"SENSOR_03": "recovery"})),
        (("SENSOR_03", "RED", "escalation"), ("SENSOR_03", "GREEN", "recovery")),
    ),
    "isolated-spike": Scenario(
        "isolated-spike", "An isolated below-threshold jump is not sustained rise; critical readings are never discarded.",
        (step(0, label="Baseline"), step(30, {"SENSOR_01": .201}, label="Small change"),
         step(60, {"SENSOR_01": .4}, label="Isolated below-threshold jump"),
         step(90, label="Return to baseline"), step(120, label="Stable baseline")),
    ),
    "sensor-dropout": Scenario(
        "sensor-dropout", "Stop SENSOR_02 while keeping other nodes fresh; wait for real stale-data detection.",
        (step(0, label="Baseline"), step(30, label="Last observation before dropout")),
        dropout_node="SENSOR_02",
    ),
}
