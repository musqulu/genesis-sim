from dataclasses import FrozenInstanceError

import pytest
from genesis.domain.config import WorldConfig
from genesis.domain.state import Cell, World
from genesis.engine.world import create_world


def test_world_has_canonical_unique_coordinates():
    world = create_world(WorldConfig())
    assert len(world.cells) == 1024
    assert len(set(world.cells)) == 1024
    assert (world.cell_at(0, 0).x, world.cell_at(0, 0).y) == (0, 0)
    assert (world.cell_at(31, 31).x, world.cell_at(31, 31).y) == (31, 31)
    assert world.cell_at(7, 19) == world.cells[19 * 32 + 7]


def test_rectangular_world_preserves_xy_convention():
    world = create_world(WorldConfig(width=3, height=2))
    assert [(c.x, c.y) for c in world.cells] == [(0, 0), (1, 0), (2, 0), (0, 1), (1, 1), (2, 1)]
    assert world.cell_at(2, 1) is world.cells[5]


@pytest.mark.parametrize("x,y", [(-1, 0), (0, -1), (32, 0), (0, 32), (True, 0), (1.5, 0)])
def test_out_of_bounds_is_not_python_negative_indexing(x, y):
    with pytest.raises(IndexError):
        create_world(WorldConfig()).cell_at(x, y)


@pytest.mark.parametrize(
    "config",
    [
        {"width": 0},
        {"height": 129},
        {"width": True},
        {"seed": -1},
        {"seed": 2**32},
        {"seed": 1.5},
        {"inflow_interval": 0},
        {"inflow_amount": -1},
        {"inflow_amount": 101},
        {"energy_cap": 0},
        {"fixture": "unknown"},
    ],
)
def test_invalid_configuration_is_rejected(config):
    with pytest.raises(ValueError):
        WorldConfig(**config)


def test_world_is_immutable_and_rejects_missing_or_misordered_cells():
    world = create_world(WorldConfig(width=2, height=1))
    with pytest.raises(FrozenInstanceError):
        world.cells[0].x = 100
    with pytest.raises(ValueError):
        World(world.config, world.cells[:1], world.ledger)
    with pytest.raises(ValueError):
        World(world.config, tuple(reversed(world.cells)), world.ledger)


@pytest.mark.parametrize(
    "fields", [{"energy": -1}, {"energy": True}, {"loose_material": 1.5}, {"packed_material": -1}]
)
def test_cell_resources_are_nonnegative_integers(fields):
    with pytest.raises(ValueError):
        Cell(0, 0, **fields)


def test_world_rejects_energy_or_packing_above_configured_caps():
    world = create_world(WorldConfig(width=1, height=1))
    with pytest.raises(ValueError):
        World(world.config, (Cell(0, 0, energy=101),), world.ledger)
    with pytest.raises(ValueError):
        World(world.config, (Cell(0, 0, packed_material=9),), world.ledger)
