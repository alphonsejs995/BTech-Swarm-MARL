# config.py — Shared constants for the Swarm Rescue simulation
# All tunables live here. Other modules import from this file.

# ---------------------------------------------------------------------------
# Grid / World
# ---------------------------------------------------------------------------
GRID_WIDTH = 20
GRID_HEIGHT = 20

# Base station is always at the top-left corner
BASE_X = 0
BASE_Y = 0

# ---------------------------------------------------------------------------
# Fleet
# ---------------------------------------------------------------------------
NUM_DRONES = 5
NUM_SURVIVORS = 8

# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------
BATTERY_MAX = 100.0
BATTERY_INITIAL = 100.0
BATTERY_DRAIN_PER_MOVE = 2.0       # flat cost per completed move
BATTERY_DRAIN_PER_SCAN = 3.0       # cost per thermal scan call
BATTERY_DRAIN_IDLE = 0.0           # no drain while hovering/idle
BATTERY_CHARGE_RATE = 5.0          # units restored per tick while charging
BATTERY_LOW_THRESHOLD = 20.0       # drone should start returning below this
BATTERY_CRITICAL = 10.0            # drone goes RETURNING if at/below this

# ---------------------------------------------------------------------------
# Sensors
# ---------------------------------------------------------------------------
SCAN_RADIUS = 3                    # cells revealed by one thermal_scan()
THERMAL_NOISE_STD = 1.5            # Gaussian noise sigma (degrees C)
FALSE_POSITIVE_RATE = 0.08         # 8% chance a hot cell is NOT a survivor
DETECTION_THRESHOLD = 34.0        # minimum temp (C) to flag as possible survivor
CONFIRM_RADIUS = 1                 # confirm_survivor checks cells within this range

# ---------------------------------------------------------------------------
# Communications
# ---------------------------------------------------------------------------
COMM_RANGE = 5                     # cells; messages outside range are dropped
RELAY_EXTENSION = 5                # extra range a relay drone adds

# ---------------------------------------------------------------------------
# Thermal environment
# ---------------------------------------------------------------------------
AMBIENT_TEMP = 28.0                # baseline outdoor temperature (C)
SURVIVOR_BODY_TEMP = 37.0          # nominal human body temperature (C)
SURVIVOR_TEMP_STD = 0.5            # natural variation sigma (C)
GROUP_TEMP_BONUS = 0.8             # extra degrees per additional survivor in group

# ---------------------------------------------------------------------------
# Urgency escalation thresholds (in ticks)
# ---------------------------------------------------------------------------
URGENCY_MEDIUM_TO_HIGH = 50        # ticks before medium -> high
URGENCY_HIGH_TO_CRITICAL = 100     # ticks before high -> critical

# ---------------------------------------------------------------------------
# Movement / Game loop
# ---------------------------------------------------------------------------
TICK_RATE_HZ = 1                   # nominal ticks per second (for display only)
MAX_TICKS = 500                    # mission timeout
PATH_COST_MULTIPLIER = 1           # reserved for future diagonal movement

# ---------------------------------------------------------------------------
# Leader election weights
# ---------------------------------------------------------------------------
LEADER_WEIGHT_BATTERY = 0.4        # battery contributes 40 of 100 pts
LEADER_WEIGHT_SENSOR = 20.0        # sensor health bonus
LEADER_WEIGHT_UPTIME = 10.0        # uptime normalised bonus
LEADER_WEIGHT_ACTIVE = 30.0        # bonus for being ACTIVE status

# ---------------------------------------------------------------------------
# Scenario defaults
# ---------------------------------------------------------------------------
DEFAULT_TERRAIN_PATH = "terrain_data/sulawesi_earthquake.json"
DEFAULT_SCENARIO = "sulawesi_earthquake"

# ---------------------------------------------------------------------------
# 3D Rendering (Pygame isometric)
# ---------------------------------------------------------------------------
TILE_WIDTH = 64          # isometric tile width in pixels
TILE_HEIGHT = 32         # isometric tile height in pixels
ELEVATION_SCALE = 12     # pixels per elevation unit
SCREEN_WIDTH = 1400
SCREEN_HEIGHT = 900
FPS = 30
CAMERA_SPEED = 10        # pixels per frame for camera pan
