from dataclasses import dataclass, field

RULES_VERSION = "m13-rules-1"
# Rules identity hashed into each state schema; old schemas keep their original value so
# that runs recorded under them still verify.
RULES_BY_SCHEMA = {
    2: "m02-rules-1",
    3: "m02-rules-1",
    4: "m04-rules-1",
    5: "m05-rules-1",
    6: "m06-rules-1",
    7: "m08-rules-1",
    8: "m09-rules-1",
    9: "m10-rules-1",
    10: "m11-rules-1",
    11: "m12-rules-1",
    12: RULES_VERSION,
}
# Lifecycle/material fields introduced with state schema 5 (M05). They are omitted from
# schema-4 serializations so M04 runs keep byte-identical hashes and stored records.
M05_LIFE_FIELDS = (
    "material_capacity",
    "extract_max",
    "extract_fee",
    "modify_max",
    "modify_fee_per_unit",
)
# Cognition scheduling fields introduced with state schema 6 (M06).
M06_LIFE_FIELDS = (
    "decision_interval",
    "cognition_fee",
    "trigger_threshold",
    "queue_capacity",
    "provider",
)
# Costly active memory introduced with state schema 7 (M08).
M08_LIFE_FIELDS = (
    "memory_enabled",
    "memory_max_records",
    "memory_max_bytes",
    "memory_record_max_bytes",
    "memory_write_fee_per_kib",
    "memory_upkeep_per_kib",
    "memory_upkeep_interval",
    "recall_max",
    "recall_max_bytes",
)
# Local exchange between organisms introduced with state schema 8 (M09).
M09_LIFE_FIELDS = (
    "exchange_enabled",
    "transfer_max",
    "transfer_fee",
    "message_max_bytes",
    "message_fee_base",
    "message_fee_per_64_bytes",
    "inbox_capacity",
    "message_ttl",
)
# Starting tendencies introduced with state schema 9 (M10).
M10_LIFE_FIELDS = ("tendency_mode",)
# Agent-initiated reproduction introduced with state schema 11 (M12).
M12_LIFE_FIELDS = (
    "reproduction_enabled",
    "reproduction_fee",
    "reproduction_min_endowment",
    "reproduction_retain",
    "reproduction_cooldown",
    "inherit_probability_percent",
    "inherit_perturbation",
)
# Selective memory inheritance introduced with state schema 12 (M13).
M13_LIFE_FIELDS = ("inherit_memory_max", "inherit_memory_retention_percent")
# Bounded reflection introduced with state schema 10 (M11).
M11_LIFE_FIELDS = (
    "reflection_enabled",
    "reflection_threshold",
    "reflection_cooldown",
    "reflection_fee",
    "reflection_max",
    "reflection_context_max",
)
# Competing dispositions (constitution §3), each an integer 0-100, fixed for a life. They
# are inclinations stated to the organism's own cognition, never rules, goals or roles.
TENDENCIES = (
    "maintenance",  # self-maintenance / energy-seeking
    "caution",  # caution / risk avoidance
    "curiosity",  # curiosity / information-seeking
    "affiliation",  # communication / affiliation
    "experimentation",  # experimentation / novelty-seeking
    "transmission",  # transmission of knowledge or traits
)
TENDENCY_MAX = 100
TENDENCY_MODES = ("seeded", "neutral", "off")  # "off": earlier fixtures, no tendencies
RNG_ALGORITHM = "python-random-mt19937"
DIRECTIONS = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}


def _check_int(name: str, value: object, low: int, high: int) -> None:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")


@dataclass(frozen=True, slots=True)
class SeedOrganism:
    """A seed-population placement. Only labeled test fixtures define seeds in M04."""

    x: int
    y: int
    policy: str  # deterministic test policy name (cognition arrives in M06/M07)
    # Labeled fixtures may fix starting tendencies (schema 9); None = drawn from the seed.
    tendencies: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        _check_int("seed x", self.x, 0, 127)
        _check_int("seed y", self.y, 0, 127)
        if not isinstance(self.policy, str) or not self.policy:
            raise ValueError("seed policy must be a name")
        if self.tendencies is not None:
            if not isinstance(self.tendencies, tuple) or len(self.tendencies) != len(TENDENCIES):
                raise ValueError("seed tendencies must be a tuple of six values")
            for value in self.tendencies:
                _check_int("tendency", value, 0, TENDENCY_MAX)

    def to_dict(self) -> dict:
        data = {"x": self.x, "y": self.y, "policy": self.policy}
        if self.tendencies is not None:  # absent keeps earlier seed records identical
            data["tendencies"] = list(self.tendencies)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SeedOrganism":
        tendencies = data.get("tendencies")
        return cls(
            data["x"], data["y"], data["policy"], None if tendencies is None else tuple(tendencies)
        )


@dataclass(frozen=True, slots=True)
class LifeConfig:
    """Lifecycle rules (proposed laboratory defaults, frozen per run)."""

    energy_capacity: int = 100
    start_energy: int = 60
    upkeep: int = 1  # basal energy per tick, dissipated
    lifespan_min: int = 160  # hidden lifespan sampled uniformly from [min, max]
    lifespan_max: int = 240
    perception_radius: int = 2  # Manhattan
    move_base_cost: int = 1  # entry cost = base + floor(packed_material / 2)
    seeds: tuple[SeedOrganism, ...] = field(default_factory=tuple)
    # M05 material rules (proposed defaults; schema 5 runs only).
    material_capacity: int = 20  # carried material per organism
    extract_max: int = 10  # units per extract action; larger requests are rejected
    extract_fee: int = 1  # energy, paid from energy held before extracting
    modify_max: int = 2  # units per pack/unpack action
    modify_fee_per_unit: int = 2  # energy per unit packed or unpacked
    # M06 cognition scheduling (proposed defaults; schema 6 runs only).
    decision_interval: int = 4  # a decision is due this many ticks after the last one
    cognition_fee: int = 1  # energy per due decision opportunity, before observing
    trigger_threshold: int = 10  # local view energy+material change that makes one due early
    queue_capacity: int = 4  # in-flight requests at most (technical bound)
    # Provider profile, fixed for the run: "scripted" (test scripts per organism policy),
    # "recorded:<recording>" (responses keyed by request input hash) or "mlx:<model id>"
    # (a local MLX-LM model), "api:<profile id>" (an HTTP model API, M07B) or "mixed"
    # (each organism has its own model, assigned at creation); the full profile is sealed
    # in the run manifest.
    provider: str = "scripted"
    # M08 costly memory (proposed defaults; schema 7 runs only). Records are written by the
    # engine after each decision; the organism pays for writing and for keeping them.
    memory_enabled: bool = True  # False keeps a fixture's earlier (memory-free) behavior
    memory_max_records: int = 64  # active records per organism
    memory_max_bytes: int = 16_384  # active UTF-8 text per organism
    memory_record_max_bytes: int = 256  # per record; longer text is truncated
    memory_write_fee_per_kib: int = 1  # energy per started KiB written in one tick
    memory_upkeep_per_kib: int = 1  # energy per started KiB of active text ...
    memory_upkeep_interval: int = 10  # ... charged on every tick that is a multiple of this
    recall_max: int = 8  # records retrieved into one decision request
    recall_max_bytes: int = 2_048  # total recalled text in one request
    # M09 local exchange (proposed defaults; schema 8 runs only). Adjacent = Manhattan 1.
    exchange_enabled: bool = True  # False keeps a fixture's earlier behavior
    transfer_max: int = 10  # units per transfer action
    transfer_fee: int = 1  # energy, paid by the giver
    message_max_bytes: int = 256  # UTF-8 bytes per message
    message_fee_base: int = 1  # message fee = base + per_64 * ceil(bytes / 64)
    message_fee_per_64_bytes: int = 1
    inbox_capacity: int = 8  # messages pending or unread per recipient (receipts excluded)
    message_ttl: int = 20  # an unread delivered item expires this many ticks after delivery
    # M10 starting tendencies (schema 9): "seeded" draws six values 0-100 per organism from
    # the world seed; "neutral" draws the same numbers (so the world is identical) but gives
    # every organism 50 for all six - the paired control condition.
    tendency_mode: str = "seeded"
    # M11 bounded reflection (schema 10). A due decision becomes a reflection request when
    # the organism holds at least `reflection_threshold` salient records acquired since its
    # last reflection and `reflection_cooldown` ticks have passed; it never gets both.
    reflection_enabled: bool = True  # False keeps a fixture's earlier behavior
    reflection_threshold: int = 8  # new salient (salience >= 2) active records
    reflection_cooldown: int = 20  # ticks between reflection opportunities
    reflection_fee: int = 2  # energy per accepted reflection response, plus storage
    reflection_max: int = 2  # stored reflections per response at most
    reflection_context_max: int = 16  # own records shown to a reflection request
    # M12 reproduction (schema 11): a parent's validated choice, never a population rule.
    reproduction_enabled: bool = True  # False keeps a fixture's earlier behavior
    reproduction_fee: int = 10  # energy dissipated per birth
    reproduction_min_endowment: int = 20  # energy the child starts with, at least
    reproduction_retain: int = 10  # energy the parent must keep after fee + endowment
    reproduction_cooldown: int = 20  # ticks between a parent's births
    inherit_probability_percent: int = 50  # chance each tendency is inherited (mask)
    inherit_perturbation: int = 10  # inherited values move by a uniform -n..+n, clipped
    # M13 memory inheritance (schema 12): a parent may pass copies of a few of its own
    # active records; each copy survives independently; the parent pays per started KiB.
    inherit_memory_max: int = 2  # records a parent may select per birth
    inherit_memory_retention_percent: int = 50  # chance each selected copy survives

    def __post_init__(self) -> None:
        _check_int("energy_capacity", self.energy_capacity, 1, 10_000)
        _check_int("start_energy", self.start_energy, 1, self.energy_capacity)
        _check_int("upkeep", self.upkeep, 0, 10_000)
        _check_int("lifespan_min", self.lifespan_min, 1, 1_000_000)
        _check_int("lifespan_max", self.lifespan_max, self.lifespan_min, 1_000_000)
        _check_int("perception_radius", self.perception_radius, 0, 32)
        _check_int("move_base_cost", self.move_base_cost, 0, 10_000)
        _check_int("material_capacity", self.material_capacity, 0, 10_000)
        _check_int("extract_max", self.extract_max, 1, 10_000)
        _check_int("extract_fee", self.extract_fee, 0, 10_000)
        _check_int("modify_max", self.modify_max, 1, 10_000)
        _check_int("modify_fee_per_unit", self.modify_fee_per_unit, 0, 10_000)
        _check_int("decision_interval", self.decision_interval, 1, 10_000)
        _check_int("cognition_fee", self.cognition_fee, 0, 10_000)
        _check_int("trigger_threshold", self.trigger_threshold, 0, 1_000_000)
        _check_int("queue_capacity", self.queue_capacity, 1, 1_000)
        if type(self.memory_enabled) is not bool:
            raise ValueError("memory_enabled must be a boolean")
        _check_int("memory_record_max_bytes", self.memory_record_max_bytes, 64, 4_096)
        # Room for a decision's own observation and outcome; extra records are capped.
        _check_int("memory_max_records", self.memory_max_records, 2, 10_000)
        _check_int(
            "memory_max_bytes", self.memory_max_bytes, 2 * self.memory_record_max_bytes, 1 << 20
        )
        _check_int("memory_write_fee_per_kib", self.memory_write_fee_per_kib, 0, 10_000)
        _check_int("memory_upkeep_per_kib", self.memory_upkeep_per_kib, 0, 10_000)
        _check_int("memory_upkeep_interval", self.memory_upkeep_interval, 1, 1_000_000)
        _check_int("recall_max", self.recall_max, 0, 64)
        _check_int("recall_max_bytes", self.recall_max_bytes, 0, 1 << 16)
        if type(self.exchange_enabled) is not bool:
            raise ValueError("exchange_enabled must be a boolean")
        _check_int("transfer_max", self.transfer_max, 1, 10_000)
        _check_int("transfer_fee", self.transfer_fee, 0, 10_000)
        _check_int("message_max_bytes", self.message_max_bytes, 1, 4_096)
        _check_int("message_fee_base", self.message_fee_base, 0, 10_000)
        _check_int("message_fee_per_64_bytes", self.message_fee_per_64_bytes, 0, 10_000)
        _check_int("inbox_capacity", self.inbox_capacity, 1, 1_000)
        _check_int("message_ttl", self.message_ttl, 1, 1_000_000)
        if type(self.reflection_enabled) is not bool:
            raise ValueError("reflection_enabled must be a boolean")
        _check_int("reflection_threshold", self.reflection_threshold, 1, 10_000)
        _check_int("reflection_cooldown", self.reflection_cooldown, 1, 1_000_000)
        _check_int("reflection_fee", self.reflection_fee, 0, 10_000)
        _check_int("reflection_max", self.reflection_max, 1, 8)
        _check_int("reflection_context_max", self.reflection_context_max, 1, 64)
        if type(self.reproduction_enabled) is not bool:
            raise ValueError("reproduction_enabled must be a boolean")
        _check_int("reproduction_fee", self.reproduction_fee, 0, 10_000)
        _check_int("reproduction_min_endowment", self.reproduction_min_endowment, 1, 10_000)
        _check_int("reproduction_retain", self.reproduction_retain, 0, 10_000)
        _check_int("reproduction_cooldown", self.reproduction_cooldown, 1, 1_000_000)
        _check_int("inherit_probability_percent", self.inherit_probability_percent, 0, 100)
        _check_int("inherit_perturbation", self.inherit_perturbation, 0, TENDENCY_MAX)
        _check_int("inherit_memory_max", self.inherit_memory_max, 0, 8)
        _check_int(
            "inherit_memory_retention_percent", self.inherit_memory_retention_percent, 0, 100
        )
        if self.tendency_mode not in TENDENCY_MODES:
            raise ValueError("tendency_mode must be 'seeded', 'neutral' or 'off'")
        if not isinstance(self.provider, str) or not (
            self.provider in ("scripted", "mixed")
            or self.provider.startswith("recorded:")
            or self.provider.startswith("mlx:")
            or self.provider.startswith("api:")
        ):
            raise ValueError(
                "provider must be 'scripted', 'recorded:<name>', 'mlx:<model>' or 'api:<profile>'"
            )
        if not isinstance(self.seeds, tuple) or not all(
            isinstance(s, SeedOrganism) for s in self.seeds
        ):
            raise ValueError("seeds must be a tuple of SeedOrganism")
        if len({(s.x, s.y) for s in self.seeds}) != len(self.seeds):
            raise ValueError("seed organisms may not share a cell")

    def to_dict(self, schema: int = 12) -> dict:
        data = {
            "energy_capacity": self.energy_capacity,
            "start_energy": self.start_energy,
            "upkeep": self.upkeep,
            "lifespan_min": self.lifespan_min,
            "lifespan_max": self.lifespan_max,
            "perception_radius": self.perception_radius,
            "move_base_cost": self.move_base_cost,
            "seeds": [s.to_dict() for s in self.seeds],
        }
        if schema >= 5:
            data.update({name: getattr(self, name) for name in M05_LIFE_FIELDS})
        if schema >= 6:
            data.update({name: getattr(self, name) for name in M06_LIFE_FIELDS})
        if schema >= 7:
            data.update({name: getattr(self, name) for name in M08_LIFE_FIELDS})
        if schema >= 8:
            data.update({name: getattr(self, name) for name in M09_LIFE_FIELDS})
        if schema >= 9:
            data.update({name: getattr(self, name) for name in M10_LIFE_FIELDS})
        if schema >= 10:
            data.update({name: getattr(self, name) for name in M11_LIFE_FIELDS})
        if schema >= 11:
            data.update({name: getattr(self, name) for name in M12_LIFE_FIELDS})
        if schema >= 12:
            data.update({name: getattr(self, name) for name in M13_LIFE_FIELDS})
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "LifeConfig":
        return cls(
            **{k: v for k, v in data.items() if k != "seeds"},
            seeds=tuple(SeedOrganism.from_dict(s) for s in data["seeds"]),
        )


@dataclass(frozen=True, slots=True)
class Fixture:
    """A clearly labeled test configuration: diagnostics, never evidence of emergence."""

    world: dict
    life: LifeConfig
    description: str
    # Fixture-defined initial loose material at cells: ((x, y, amount), ...). Applied
    # after seeding and recorded in the initial state, so replay never re-applies it.
    stockpiles: tuple[tuple[int, int, int], ...] = ()


# Selectable only in development/test mode. A fixture creates a new labeled run; it never
# edits an ongoing run. Fixture policies are scripted and deterministic.
# Earlier fixtures keep their M04/M05 behavior under M06 rules: a decision every tick and no
# cognition fee. Their scripted policies now answer through the provider contract.
_EVERY_TICK = {"decision_interval": 1, "cognition_fee": 0, "trigger_threshold": 0}
# Fixtures from M04-M06 keep their behavior (and recordings) under M08 rules: no memory.
_NO_MEMORY = {
    "memory_enabled": False,
    "exchange_enabled": False,
    "tendency_mode": "off",
    "reflection_enabled": False,
    "reproduction_enabled": False,
}
# M08 fixtures keep their behavior under M09 rules: memory, but no exchange actions.
_NO_EXCHANGE = {
    "exchange_enabled": False,
    "tendency_mode": "off",
    "reflection_enabled": False,
    "reproduction_enabled": False,
}
# M09 fixtures keep their behavior under M10 rules: exchange, but no tendencies.
_NO_TENDENCIES = {
    "tendency_mode": "off",
    "reflection_enabled": False,
    "reproduction_enabled": False,
}
# M10 fixtures keep their behavior under M11 rules: tendencies, but no reflection.
_NO_REFLECTION = {"reflection_enabled": False, "reproduction_enabled": False}
# M11 fixtures keep their behavior under M12 rules: reflection, but no reproduction.
_NO_REPRODUCTION = {"reproduction_enabled": False}

FIXTURES: dict[str, Fixture] = {
    "zero_inflow": Fixture({"inflow_amount": 0}, LifeConfig(), "No energy inflow, no organisms"),
    "corner_perception": Fixture(
        {},
        LifeConfig(**_EVERY_TICK, **_NO_MEMORY, seeds=(SeedOrganism(0, 0, "corner_tour"),)),
        "One test organism at (0,0); scripted moves incl. off-grid attempts",
    ),
    "starvation": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_MEMORY,
            start_energy=13,
            seeds=(SeedOrganism(16, 16, "square_walk"),),
        ),
        "One test organism with 13 energy walking a square until it starves",
    ),
    "material_workshop": Fixture(
        {},
        LifeConfig(**_EVERY_TICK, **_NO_MEMORY, seeds=(SeedOrganism(10, 10, "workshop"),)),
        "One test organism at a 25-unit material stockpile; scripted extraction, packing, "
        "unpacking, over-limit/unaffordable attempts, then waiting until death",
        stockpiles=((10, 10, 25),),
    ),
    "age_limit": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_MEMORY,
            start_energy=100,
            lifespan_min=20,
            lifespan_max=30,
            seeds=(SeedOrganism(5, 5, "wait"),),
        ),
        "One waiting test organism with a short sampled lifespan (20-30 ticks)",
    ),
    # M06: decisions through recorded responses keyed by request input hash. Default
    # cadence (every 4 ticks or on a local-view trigger) and a 1-energy cognition fee.
    "recorded_valid": Fixture(
        {},
        LifeConfig(
            **_NO_MEMORY,
            provider="recorded:recorded_valid",
            seeds=(SeedOrganism(8, 8, "recorded"),),
        ),
        "One organism whose decisions are recorded valid responses",
        stockpiles=((8, 8, 12),),
    ),
    "recorded_invalid": Fixture(
        {},
        LifeConfig(
            **_NO_MEMORY,
            provider="recorded:recorded_invalid",
            seeds=(SeedOrganism(3, 0, "recorded"),),
        ),
        "Recorded malformed, wrong-type, out-of-range, unknown and illegal outputs",
    ),
    "recorded_delayed": Fixture(
        {},
        LifeConfig(
            **_NO_MEMORY,
            provider="recorded:recorded_delayed",
            seeds=(SeedOrganism(8, 8, "recorded"),),
        ),
        "Recorded valid responses that arrive after a delay (pause while pending)",
        stockpiles=((8, 8, 12),),
    ),
    "recorded_mismatch": Fixture(
        {},
        LifeConfig(
            **_NO_MEMORY,
            provider="recorded:recorded_mismatch",
            seeds=(SeedOrganism(8, 8, "recorded"),),
        ),
        "A recording whose second entry does not match its request (technical pause)",
        stockpiles=((8, 8, 12),),
    ),
    # M08: costly memory and bounded recall. Scripted, labeled test policies.
    "memory_return": Fixture(
        {},
        # recall_max 16 (labeled): every record of this short walk stays recallable.
        LifeConfig(
            **_EVERY_TICK,
            **_NO_EXCHANGE,
            recall_max=16,
            seeds=(SeedOrganism(10, 10, "recall_return"),),
        ),
        "One test organism notes a 25-unit material stockpile, walks out of view, then "
        "walks back only if a recalled memory of material points there (recall_max 16)",
        stockpiles=((10, 10, 25),),
    ),
    "memory_budget": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_EXCHANGE,
            start_energy=40,
            memory_max_records=6,
            memory_max_bytes=1_024,
            seeds=(SeedOrganism(16, 16, "square_walk"),),
        ),
        "One test organism walking a square with a 6-record memory: write fees, upkeep, "
        "oldest-first eviction, then unaffordable writes until it starves",
    ),
    # M09: two scripted neighbours exchange resources and messages (diagnostic only).
    "exchange_pair": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_TENDENCIES,
            seeds=(
                SeedOrganism(12, 12, "exchange_giver"),
                SeedOrganism(13, 12, "exchange_replier"),
            ),
        ),
        "Two scripted neighbours: transfers of energy and material, a message delivered "
        "the next tick and answered, out-of-range and over-capacity attempts, then waiting",
        stockpiles=((12, 12, 15),),
    ),
    # M10: matched pair differing only in org-0001's starting tendencies (affiliation).
    "tendency_social": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_REFLECTION,
            seeds=(
                SeedOrganism(12, 12, "tendency_probe", (50, 50, 50, 90, 50, 50)),
                SeedOrganism(13, 12, "wait", (50, 50, 50, 50, 50, 50)),
            ),
        ),
        "Matched pair A: org-0001 has affiliation 90; a scripted tendency-reading policy "
        "messages its neighbour when affiliation is at least 50, otherwise extracts energy",
    ),
    "tendency_solitary": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_REFLECTION,
            seeds=(
                SeedOrganism(12, 12, "tendency_probe", (50, 50, 50, 10, 50, 50)),
                SeedOrganism(13, 12, "wait", (50, 50, 50, 50, 50, 50)),
            ),
        ),
        "Matched pair B: identical to tendency_social except org-0001's affiliation is 10",
    ),
    # M11: reflection opportunities from accumulated salient records (scripted replies).
    "reflection_probe": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_REPRODUCTION,
            start_energy=100,
            seeds=(SeedOrganism(16, 16, "walk_reflect"),),
        ),
        "One test organism walking a square; once 8 new salient records exist (and every "
        "20 ticks after), its decision becomes a reflection citing two of its own records",
    ),
    "reflection_invalid": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            **_NO_REPRODUCTION,
            start_energy=100,
            seeds=(SeedOrganism(16, 16, "walk_reflect_invalid"),),
        ),
        "Like reflection_probe, but every reflection cites a memory id it never had: "
        "rejected, nothing stored, the opportunity and its cooldown still used",
    ),
    # M12: a scripted parent's valid and invalid birth attempts (diagnostic only).
    "birth_pair": Fixture(
        {"inflow_amount": 0},
        LifeConfig(
            **_EVERY_TICK,
            memory_enabled=False,
            exchange_enabled=False,
            reflection_enabled=False,
            start_energy=100,
            lifespan_min=24,
            lifespan_max=60,
            seeds=(SeedOrganism(12, 12, "reproducer", (80, 20, 60, 40, 70, 30)),),
        ),
        "One scripted parent: a birth east (endowment 40), then attempts rejected for "
        "cooldown, a too-small endowment, an occupied cell and too little energy; the child "
        "runs the same script and is too poor to reproduce. No energy inflow",
    ),
    # M13: a scripted parent passes copies of selected memories to its child.
    "birth_memory": Fixture(
        {},
        LifeConfig(
            **_EVERY_TICK,
            exchange_enabled=False,
            reflection_enabled=False,
            start_energy=100,
            # Labeled: 75% (default 50%) so seed 42 shows one copy kept and one dropped
            # (retention rolls 74 and 79); other seeds give other outcomes.
            inherit_memory_retention_percent=75,
            seeds=(SeedOrganism(12, 12, "memory_parent", (50, 50, 50, 50, 50, 50)),),
        ),
        "One scripted parent gathers records for 8 ticks, tries to pass an id it never had "
        "(invalid_memory) and three ids (over_action_limit), then gives birth passing its "
        "two newest records: each copy survives with 75% (labeled); the child lives on its own",
        stockpiles=((12, 12, 20),),
    ),
}


@dataclass(frozen=True, slots=True)
class WorldConfig:
    width: int = 32
    height: int = 32
    seed: int = 42
    energy_cap: int = 100
    packed_cap: int = 8  # The field exists in M02; no rule can change it yet.
    inflow_interval: int = 10  # Inflow occurs when the new tick is a multiple of this.
    inflow_amount: int = 1  # Requested energy per cell; 0 disables the rule.
    material_density_percent: int = 25  # Seeded share of cells holding loose material.
    material_max: int = 10  # Seeded loose material per such cell: 1..material_max.
    fixture: str | None = None

    def __post_init__(self) -> None:
        _check_int("width", self.width, 1, 128)
        _check_int("height", self.height, 1, 128)
        _check_int("seed", self.seed, 0, 2**32 - 1)
        _check_int("energy_cap", self.energy_cap, 1, 10_000)
        _check_int("packed_cap", self.packed_cap, 0, 1_000)
        _check_int("inflow_interval", self.inflow_interval, 1, 1_000_000)
        _check_int("inflow_amount", self.inflow_amount, 0, self.energy_cap)
        _check_int("material_density_percent", self.material_density_percent, 0, 100)
        _check_int("material_max", self.material_max, 1, 1_000)
        if self.fixture is not None and self.fixture not in FIXTURES:
            raise ValueError(f"unknown fixture: {self.fixture!r}")

    @classmethod
    def for_run(
        cls,
        seed: int,
        fixture: str | None = None,
        size: int = 32,
        inflow_amount: int | None = None,
    ) -> "WorldConfig":
        """A new run's world. `size` makes a square map and `inflow_amount` sets the energy
        added per cell on each inflow tick (fixtures keep their own layout and inflow)."""
        if fixture is not None and fixture not in FIXTURES:
            raise ValueError(f"unknown fixture: {fixture!r}")
        if fixture is not None:
            return cls(seed=seed, fixture=fixture, **FIXTURES[fixture].world)
        extra = {} if inflow_amount is None else {"inflow_amount": inflow_amount}
        return cls(seed=seed, width=size, height=size, **extra)


def life_for_run(fixture: str | None) -> LifeConfig:
    """Standard runs have no seed organisms until cognition exists (M06/M07)."""
    return FIXTURES[fixture].life if fixture else LifeConfig()
