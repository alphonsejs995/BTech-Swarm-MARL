# simulation/renderer.py
# Renderers for the Swarm Rescue simulation.
#
# Two renderers are provided:
#   TerminalRenderer   — original ANSI colour ASCII-art grid (headless/CI safe)
#   IsometricRenderer  — Pygame 3-D isometric view with free camera rotation
#
# Controls (IsometricRenderer):
#   Right-click drag   — rotate camera (free angle, like Blender)
#   WASD / Arrows      — pan camera
#   Mouse scroll        — zoom in / out
#   R                  — reset camera
#   G                  — toggle grid overlay

from __future__ import annotations

import os
import sys
import math
import random
import shutil
import subprocess

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from simulation.world import World

from simulation.drone import DroneRole, DroneStatus
from simulation.terrain import TerrainType, TERRAIN_PROPERTIES
from config import BASE_X, BASE_Y

# ---------------------------------------------------------------------------
# Shared colour tables
# ---------------------------------------------------------------------------

_RESET = "\033[0m"
_BOLD = "\033[1m"
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_DIM = "\033[2m"

_TERMINAL_TERRAIN_COLOURS = {
    TerrainType.CLEAR_ROAD: _WHITE,
    TerrainType.JUNGLE: _GREEN,
    TerrainType.FLOOD_ZONE: _BLUE,
    TerrainType.URBAN_RUBBLE: _YELLOW,
    TerrainType.MOUNTAIN: _DIM,
    TerrainType.VOLCANIC_ASH: _RED,
    TerrainType.COASTLINE: _CYAN,
}

_ISO_TERRAIN_TOP = {
    TerrainType.CLEAR_ROAD:   (180, 190, 170),
    TerrainType.JUNGLE:       (34,  139,  34),
    TerrainType.FLOOD_ZONE:   (45,  90, 180),
    TerrainType.URBAN_RUBBLE: (158, 124,  76),
    TerrainType.MOUNTAIN:     (128, 128, 128),
    TerrainType.VOLCANIC_ASH: (100,  55,  20),
    TerrainType.COASTLINE:    (220, 200, 160),
}

_ROLE_COLOURS = {
    DroneRole.SCOUT:  (0,   255, 255),
    DroneRole.RESCUE: (255,  50,  50),
    DroneRole.RELAY:  (255, 230,   0),
    DroneRole.IDLE:   (230, 230, 230),
}
_OFFLINE_COLOUR = (80, 80, 80)

_URGENCY_PULSE = {
    "low":      0.5,
    "medium":   1.0,
    "high":     2.0,
    "critical": 4.0,
}


# ===========================================================================
# TerminalRenderer  (original, kept intact)
# ===========================================================================

class TerminalRenderer:
    """Renders the World state to stdout as a coloured ASCII grid."""

    def __init__(self, use_colour: bool = True, clear_screen: bool = True) -> None:
        self.use_colour = use_colour and self._terminal_supports_colour()
        self.clear_screen = clear_screen

    def render(self, world: "World") -> None:
        if self.clear_screen:
            self._clear()
        lines = self._build_frame(world)
        print("\n".join(lines))

    def render_to_string(self, world: "World") -> str:
        return "\n".join(self._build_frame(world))

    def _build_frame(self, world: "World") -> list[str]:
        lines: list[str] = []
        lines.append(
            f"  === Swarm Rescue  tick={world.tick_count:04d}  "
            f"rescued={world.rescued_count}/{len(world.survivors)}  "
            f"coverage={world._calc_coverage():.1f}% ==="
        )
        lines.append("")
        header_tens = "    " + "".join(
            str(x // 10) if x % 10 == 0 else " "
            for x in range(world.terrain.width)
        )
        lines.append(header_tens)
        header_units = "    " + "".join(str(x % 10) for x in range(world.terrain.width))
        lines.append(header_units)
        lines.append("   +" + "-" * world.terrain.width + "+")

        drone_cells: dict[tuple[int, int], list] = {}
        for drone in world.drones.values():
            drone_cells.setdefault((drone.x, drone.y), []).append(drone)
        survivor_cells: dict[tuple[int, int], list] = {}
        for s in world.survivors:
            if not s.rescued:
                survivor_cells.setdefault((s.x, s.y), []).append(s)

        for y in range(world.terrain.height):
            row_chars = [self._cell_char(world, x, y, drone_cells, survivor_cells)
                         for x in range(world.terrain.width)]
            lines.append(f"{y:2d} |{''.join(row_chars)}|")
        lines.append("   +" + "-" * world.terrain.width + "+")
        lines.append("")
        lines.append("  Drones:")
        for drone in world.drones.values():
            bat_bar = self._battery_bar(drone.battery)
            leader_marker = " [LEADER]" if drone.is_leader else ""
            lines.append(
                f"    {drone.drone_id:<10} ({drone.x:2d},{drone.y:2d})  "
                f"bat={bat_bar} {drone.battery:5.1f}%  "
                f"role={drone.role.value:<7}  "
                f"status={drone.status.value:<10}"
                f"{leader_marker}"
            )
        lines.append("")
        lines.append("  Legend: D=drone  S=survivor  B=base  .=road  *=jungle")
        lines.append("          ~=flood  #=rubble   ^=mountain  %=ash  ==coast  ?=fog")
        return lines

    def _cell_char(self, world, x, y, drone_cells, survivor_cells):
        if not world.fog_of_war[y][x]:
            return self._colour("?", _DIM)
        if x == BASE_X and y == BASE_Y:
            return self._colour("B", _BOLD + _WHITE)
        if (x, y) in drone_cells:
            drones_here = drone_cells[(x, y)]
            d = next((d for d in drones_here if d.is_leader), drones_here[0])
            symbol = "D" if d.is_leader else "d"
            colour = _BOLD + _CYAN if d.is_leader else _CYAN
            if d.status == DroneStatus.OFFLINE:
                colour = _RED
            return self._colour(symbol, colour)
        if (x, y) in survivor_cells:
            return self._colour("S", _BOLD + _RED)
        terrain_type = world.terrain.get(x, y)
        symbol = world.terrain.to_symbol(x, y)
        colour = _TERMINAL_TERRAIN_COLOURS.get(terrain_type, _WHITE)
        return self._colour(symbol, colour)

    def _colour(self, text, code):
        if not self.use_colour:
            return text
        return f"{code}{text}{_RESET}"

    @staticmethod
    def _battery_bar(battery, width=10):
        filled = round(battery / 100.0 * width)
        return "[" + "#" * filled + "." * (width - filled) + "]"

    @staticmethod
    def _terminal_supports_colour():
        return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

    @staticmethod
    def _clear():
        print("\033[H\033[J", end="")


# ===========================================================================
# Pseudo-noise helpers (no external deps)
# ===========================================================================

def _smooth_noise(x: float, y: float, seed: int = 0) -> float:
    """Simple value-noise function using integer grid interpolation.
    Returns a float in [-1, 1]. Pure Python, no dependencies.
    """
    def _hash(ix: int, iy: int) -> float:
        n = ix + iy * 57 + seed * 131
        n = (n << 13) ^ n
        return 1.0 - ((n * (n * n * 15731 + 789221) + 1376312589) & 0x7FFFFFFF) / 1073741824.0

    ix = int(math.floor(x))
    iy = int(math.floor(y))
    fx = x - ix
    fy = y - iy

    # Smoothstep
    ux = fx * fx * (3 - 2 * fx)
    uy = fy * fy * (3 - 2 * fy)

    v00 = _hash(ix,     iy)
    v10 = _hash(ix + 1, iy)
    v01 = _hash(ix,     iy + 1)
    v11 = _hash(ix + 1, iy + 1)

    return (v00 * (1 - ux) * (1 - uy) +
            v10 * ux * (1 - uy) +
            v01 * (1 - ux) * uy +
            v11 * ux * uy)


def _fractal_noise(x: float, y: float, octaves: int = 3, seed: int = 0) -> float:
    """Fractal Brownian Motion using _smooth_noise. Returns [-1, 1] approx."""
    value = 0.0
    amplitude = 0.5
    frequency = 1.0
    for i in range(octaves):
        value += _smooth_noise(x * frequency, y * frequency, seed + i * 17) * amplitude
        amplitude *= 0.5
        frequency *= 2.0
    return max(-1.0, min(1.0, value))


# ===========================================================================
# Continuous heightmap mesh — replaces isolated tile polygons
# ===========================================================================

_MESH_SUBDIV = 3   # each tile is subdivided into SUBDIV x SUBDIV quads


def _build_vertex_heights(terrain) -> dict:
    """Pre-compute a heightmap at sub-tile vertex resolution.

    Vertices live on a grid of size (W*SUBDIV+1) x (H*SUBDIV+1) where W,H
    are the terrain tile dimensions.  Each vertex at sub-tile position
    (svx, svy) within tile (gx, gy) blends the tile's own elevation at the
    tile center with an averaged corner height at tile edges, so mountains
    preserve their peak while transitions to neighbours are smooth.

    Additionally a small amount of fractal noise is added for natural variety.
    """
    S = _MESH_SUBDIV
    W = terrain.width
    H = terrain.height

    # Step 1 — compute corner heights at tile-grid intersections (vx, vy).
    # A corner (vx, vy) is shared by up to 4 tiles: the ones at
    # (vx-1, vy-1), (vx, vy-1), (vx-1, vy), (vx, vy).
    corner_h: dict[tuple[int, int], float] = {}
    for vy in range(H + 1):
        for vx in range(W + 1):
            total = 0.0
            count = 0
            for dx, dy in [(0, 0), (-1, 0), (0, -1), (-1, -1)]:
                tx, ty = vx + dx, vy + dy
                if 0 <= tx < W and 0 <= ty < H:
                    total += terrain.elevation(tx, ty)
                    count += 1
            corner_h[(vx, vy)] = total / count if count else 0.0

    # Step 2 — for each sub-vertex within each tile, blend between the tile's
    # own elevation (dominant at the center) and the bilinear interpolation of
    # its four corner heights (dominant at edges).
    heights: dict[tuple[int, int], float] = {}
    for gy in range(H):
        for gx in range(W):
            tile_elev = float(terrain.elevation(gx, gy))
            # 4 corner heights of this tile
            ch00 = corner_h[(gx,     gy    )]
            ch10 = corner_h[(gx + 1, gy    )]
            ch01 = corner_h[(gx,     gy + 1)]
            ch11 = corner_h[(gx + 1, gy + 1)]

            for sy in range(S + 1):
                for sx in range(S + 1):
                    svx = gx * S + sx
                    svy = gy * S + sy
                    if (svx, svy) in heights:
                        continue   # already set by a previous tile (shared edge)

                    u = sx / S    # 0 .. 1 within tile
                    v = sy / S    # 0 .. 1 within tile

                    # Bilinear interpolation of the 4 corner heights
                    interp_h = (ch00 * (1 - u) * (1 - v) +
                                ch10 * u       * (1 - v) +
                                ch01 * (1 - u) * v       +
                                ch11 * u       * v)

                    # Chebyshev distance from tile center in [0,1]
                    edge = max(abs(2 * u - 1), abs(2 * v - 1))
                    blend = edge * edge * (3.0 - 2.0 * edge)   # smoothstep

                    # Center-preserving blend: tile's own elev at center,
                    # interpolated corner height at edges.
                    base_h = tile_elev * (1.0 - blend) + interp_h * blend

                    # Fractal noise for natural surface variation
                    noise = _fractal_noise(svx * 0.5, svy * 0.5,
                                          octaves=3, seed=42)
                    base_h += noise * 0.4

                    heights[(svx, svy)] = base_h

    return heights


def _quad_colour(base_colour: tuple, svx: int, svy: int) -> tuple:
    """Return a per-sub-quad fill colour with subtle noise-based variation."""
    nv = _smooth_noise(svx * 0.7, svy * 0.7, seed=77)
    shade = 0.85 + nv * 0.22
    return tuple(max(0, min(255, int(c * shade))) for c in base_colour)


# ===========================================================================
# Gradient shading helpers for terrain tops
# ===========================================================================

def _gradient_shade(base_colour, noise_val, terrain_type):
    """Return a slightly varied colour based on noise_val and terrain type.
    This makes tiles of the same type look subtly different from each other.
    """
    r, g, b = base_colour

    if terrain_type == TerrainType.MOUNTAIN:
        # Snow cap tinting: brighter near peak, darker at base
        snow = max(0.0, noise_val * 0.5 + 0.2)
        r = min(255, int(r + snow * 80))
        g = min(255, int(g + snow * 80))
        b = min(255, int(b + snow * 90))
        # Dark face variation
        shade = 0.85 + noise_val * 0.15
        r = max(0, min(255, int(r * shade)))
        g = max(0, min(255, int(g * shade)))
        b = max(0, min(255, int(b * shade)))

    elif terrain_type == TerrainType.JUNGLE:
        # Varied greens — lighter patches, darker shadow
        shade = 0.80 + noise_val * 0.30
        r = max(0, min(255, int(r * shade)))
        g = max(0, min(255, int(g * (shade + 0.05))))
        b = max(0, min(255, int(b * shade)))

    elif terrain_type == TerrainType.FLOOD_ZONE:
        # Animated water sheen: slight brightness variation
        shade = 0.85 + noise_val * 0.20
        r = max(0, min(255, int(r * shade)))
        g = max(0, min(255, int(g * shade)))
        b = max(0, min(255, int(b * (shade + 0.10))))

    elif terrain_type == TerrainType.URBAN_RUBBLE:
        # Rubble — dusty browns, some darker patches
        shade = 0.75 + noise_val * 0.35
        r = max(0, min(255, int(r * shade)))
        g = max(0, min(255, int(g * (shade - 0.05))))
        b = max(0, min(255, int(b * (shade - 0.10))))

    elif terrain_type == TerrainType.VOLCANIC_ASH:
        # Hot ash glow — slight reddish undertone
        shade = 0.70 + noise_val * 0.35
        r = max(0, min(255, int(r * shade + noise_val * 20)))
        g = max(0, min(255, int(g * shade)))
        b = max(0, min(255, int(b * (shade - 0.10))))

    elif terrain_type == TerrainType.COASTLINE:
        # Sandy gradients: warm beige to cool wet sand
        shade = 0.85 + noise_val * 0.20
        r = max(0, min(255, int(r * shade)))
        g = max(0, min(255, int(g * (shade - 0.05))))
        b = max(0, min(255, int(b * (shade - 0.15))))

    else:
        # Road — subtle variation
        shade = 0.90 + noise_val * 0.15
        r = max(0, min(255, int(r * shade)))
        g = max(0, min(255, int(g * shade)))
        b = max(0, min(255, int(b * shade)))

    return (r, g, b)


# ===========================================================================
# IsometricRenderer — free-rotation 3D isometric Pygame view
# ===========================================================================

class IsometricRenderer:
    """3-D isometric Pygame renderer with polygon-mesh terrain tiles,
    free camera rotation (like Blender), terrain decorations, and
    real-time drone/survivor visualisation.

    Terrain tiles use organic polygon shapes instead of flat rectangles:
    - Mountains: jagged polygons with gradient snow shading
    - Water/Flood: animated wave-edge polygons
    - Jungle: rounded bulging canopy polygons
    - Rubble: broken irregular polygons
    - Coastline: wavy shoreline polygons
    - Volcanic ash: rough crusty polygons

    Elevation-based 3-D side faces use polygon walls that extend to a
    common baseline, eliminating elevation gaps.

    Tile shapes are cached per (gx, gy) at static zoom buckets to ensure
    smooth frame rates across the 20x20 grid.

    The camera can be rotated to any angle by right-click dragging.
    Tiles are depth-sorted every frame for correct painter's algorithm.
    """

    # HUD
    _HUD_PANEL_W = 260
    _HUD_PAD = 10

    def __init__(self, world: "World") -> None:
        try:
            import pygame
        except ImportError as exc:
            raise ImportError(
                "pygame is required for IsometricRenderer. "
                "Install it with: pip install pygame"
            ) from exc

        self._pygame = pygame

        from config import (
            TILE_WIDTH, TILE_HEIGHT, ELEVATION_SCALE,
            SCREEN_WIDTH, SCREEN_HEIGHT, FPS, CAMERA_SPEED,
        )
        self.TILE_W = TILE_WIDTH
        self.TILE_H = TILE_HEIGHT
        self.ELEV_SCALE = ELEVATION_SCALE
        self.SCREEN_W = SCREEN_WIDTH
        self.SCREEN_H = SCREEN_HEIGHT
        self.FPS = FPS
        self.CAMERA_SPEED = CAMERA_SPEED

        self.world = world

        pygame.init()
        self.screen = pygame.display.set_mode((self.SCREEN_W, self.SCREEN_H))
        pygame.display.set_caption("Swarm Rescue — 3D Isometric View")
        self.clock = pygame.time.Clock()

        # Camera state
        self.camera_x = self.SCREEN_W // 2
        self.camera_y = self.SCREEN_H // 3
        self.zoom = 1.0
        self.cam_angle = 0.0      # continuous rotation angle (radians)
        self.show_grid = False
        self.running = True

        # Mouse rotation state
        self._rotating = False
        self._rotate_last_x = 0

        # Fonts
        self._font_sm = pygame.font.SysFont("monospace", 12)
        self._font_md = pygame.font.SysFont("monospace", 14, bold=True)

        # Animation tick
        self._frame = 0

        # Per-tile RNG cache (seeded, deterministic for static tiles)
        self._deco_seed: dict[tuple[int, int], random.Random] = {}

        # Noise value cache per tile (computed once at init)
        self._noise_cache: dict[tuple[int, int], float] = {}

        # Compute baseline elevation (lowest in terrain - 1) to eliminate gaps
        terrain = self.world.terrain
        min_elev = 0
        for y in range(terrain.height):
            for x in range(terrain.width):
                e = terrain.elevation(x, y)
                if e < min_elev:
                    min_elev = e
        self._base_elev = min_elev - 1

        # Grid center (rotation pivot)
        self._grid_cx = terrain.width / 2.0
        self._grid_cy = terrain.height / 2.0

        # Pre-cache sin/cos (updated per frame)
        self._cos_a = 1.0
        self._sin_a = 0.0

        # Wall visibility flags (updated each frame in render())
        self._south_vis = True
        self._east_vis = True

        # Video recording state
        self._recording = False
        self._ffmpeg_proc = None
        self._record_path = None

        # Pre-compute noise values for all tiles
        self._precompute_noise()

        # Build the continuous vertex heightmap (computed once at init)
        self._heights = _build_vertex_heights(self.world.terrain)

    # ------------------------------------------------------------------
    # Pre-computation
    # ------------------------------------------------------------------

    def _precompute_noise(self):
        """Pre-compute fractal noise values for each tile at initialisation."""
        terrain = self.world.terrain
        for y in range(terrain.height):
            for x in range(terrain.width):
                nv = _fractal_noise(x * 0.4, y * 0.4, octaves=3, seed=42)
                self._noise_cache[(x, y)] = nv

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def _project(self, wx: float, wy: float, wz: float = 0) -> tuple[int, int]:
        """Project world coordinates (wx, wy, wz) to screen pixels.
        Applies continuous rotation around the grid center."""
        dx = wx - self._grid_cx
        dy = wy - self._grid_cy
        rx = dx * self._cos_a - dy * self._sin_a
        ry = dx * self._sin_a + dy * self._cos_a

        tw = self.TILE_W * self.zoom
        th = self.TILE_H * self.zoom
        es = self.ELEV_SCALE * self.zoom

        sx = (rx - ry) * (tw * 0.5) + self.camera_x
        sy = (rx + ry) * (th * 0.5) - wz * es + self.camera_y
        return int(sx), int(sy)

    def _tile_depth(self, gx: int, gy: int) -> float:
        """Depth value for painter's algorithm sorting."""
        x, y = gx + 0.5, gy + 0.5
        rx = x * self._cos_a - y * self._sin_a
        ry = x * self._sin_a + y * self._cos_a
        return rx + ry

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _darken(colour: tuple, factor: float) -> tuple:
        return (min(255, int(colour[0] * factor)),
                min(255, int(colour[1] * factor)),
                min(255, int(colour[2] * factor)))

    def _tile_on_screen(self, gx: int, gy: int) -> bool:
        """Return True if any corner of tile (gx,gy) is within the viewport."""
        margin = int(self.TILE_W * self.zoom * 2)
        elev = self.world.terrain.elevation(gx, gy)
        for dx, dy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
            sx, sy = self._project(gx + dx, gy + dy, elev)
            if (-margin < sx < self.SCREEN_W + margin and
                    -margin < sy < self.SCREEN_H + margin):
                return True
        return False

    def _tile_rng(self, gx: int, gy: int) -> random.Random:
        key = (gx, gy)
        if key not in self._deco_seed:
            self._deco_seed[key] = random.Random()
        self._deco_seed[key].seed(gx * 1000 + gy + 42)
        return self._deco_seed[key]

    def _handle_zoom(self, direction: int) -> None:
        step = 0.1 * direction
        self.zoom = max(0.3, min(3.0, self.zoom + step))

    def _reset_camera(self) -> None:
        self.camera_x = self.SCREEN_W // 2
        self.camera_y = self.SCREEN_H // 3
        self.zoom = 1.0
        self.cam_angle = 0.0

    # ------------------------------------------------------------------
    # Video recording (ffmpeg pipe — no extra Python deps)
    # ------------------------------------------------------------------

    def start_recording(self, path: str = None) -> None:
        """Start recording frames to an MP4 video via ffmpeg.

        Args:
            path: Output file path. Defaults to ``mission_recordings/recording_<timestamp>.mp4``.
        """
        if self._recording:
            return

        if not shutil.which("ffmpeg"):
            print("[RECORD] ffmpeg not found — install it to enable video recording")
            return

        if path is None:
            from datetime import datetime
            rec_dir = os.path.join(os.path.dirname(__file__), "..", "mission_recordings")
            os.makedirs(rec_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(rec_dir, f"recording_{ts}.mp4")

        self._record_path = path
        self._ffmpeg_proc = subprocess.Popen(
            [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-pix_fmt", "rgb24",
                "-s", f"{self.SCREEN_W}x{self.SCREEN_H}",
                "-r", str(self.FPS),
                "-i", "-",
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "23",
                "-pix_fmt", "yuv420p",
                path,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._recording = True
        print(f"[RECORD] Recording started -> {path}")

    def stop_recording(self) -> None:
        """Stop recording and finalise the MP4 file."""
        if not self._recording:
            return
        self._recording = False
        if self._ffmpeg_proc and self._ffmpeg_proc.stdin:
            try:
                self._ffmpeg_proc.stdin.close()
            except BrokenPipeError:
                pass
            self._ffmpeg_proc.wait(timeout=10)
        self._ffmpeg_proc = None
        print(f"[RECORD] Recording saved -> {self._record_path}")

    def _capture_frame(self) -> None:
        """Write the current screen pixels to the ffmpeg pipe."""
        if not self._recording or not self._ffmpeg_proc:
            return
        pg = self._pygame
        try:
            raw = pg.image.tobytes(self.screen, "RGB")
            self._ffmpeg_proc.stdin.write(raw)
        except (BrokenPipeError, OSError):
            self._recording = False
            print("[RECORD] ffmpeg pipe broken — recording stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def handle_events(self) -> bool:
        pg = self._pygame
        for event in pg.event.get():
            if event.type == pg.QUIT:
                self.running = False
                return False

            if event.type == pg.KEYDOWN:
                if event.key == pg.K_r:
                    self._reset_camera()
                elif event.key == pg.K_g:
                    self.show_grid = not self.show_grid
                elif event.key == pg.K_F9:
                    if self._recording:
                        self.stop_recording()
                    else:
                        self.start_recording()

            if event.type == pg.MOUSEWHEEL:
                self._handle_zoom(event.y)

            # Right-click drag to rotate (Blender-style orbit)
            if event.type == pg.MOUSEBUTTONDOWN and event.button == 3:
                self._rotating = True
                self._rotate_last_x = event.pos[0]
            if event.type == pg.MOUSEBUTTONUP and event.button == 3:
                self._rotating = False
            if event.type == pg.MOUSEMOTION and self._rotating:
                dx = event.pos[0] - self._rotate_last_x
                self.cam_angle += dx * 0.005
                self._rotate_last_x = event.pos[0]

        # Held-key panning
        keys = pg.key.get_pressed()
        sp = self.CAMERA_SPEED
        if keys[pg.K_LEFT]  or keys[pg.K_a]: self.camera_x += sp
        if keys[pg.K_RIGHT] or keys[pg.K_d]: self.camera_x -= sp
        if keys[pg.K_UP]    or keys[pg.K_w]: self.camera_y += sp
        if keys[pg.K_DOWN]  or keys[pg.K_s]: self.camera_y -= sp
        # Q/E for keyboard rotation
        if keys[pg.K_q]:
            self.cam_angle -= 0.02
        if keys[pg.K_e]:
            self.cam_angle += 0.02

        return True

    def render(self) -> None:
        pg = self._pygame

        # Update cached trig
        self._cos_a = math.cos(self.cam_angle)
        self._sin_a = math.sin(self.cam_angle)

        # Wall visibility flags based on camera angle
        self._south_vis = (math.cos(self.cam_angle) - math.sin(self.cam_angle)) > 0
        self._east_vis  = (math.cos(self.cam_angle) + math.sin(self.cam_angle)) > 0

        # Background gradient
        for y_band in range(0, self.SCREEN_H, 4):
            t = abs(y_band - self.SCREEN_H // 2) / max(1, self.SCREEN_H // 2)
            v = int(15 + (1.0 - t) * 10)
            pg.draw.rect(self.screen, (v, v, v + 8), (0, y_band, self.SCREEN_W, 4))

        # Entity lookup
        drone_cells: dict[tuple[int, int], list] = {}
        for drone in self.world.drones.values():
            drone_cells.setdefault((drone.x, drone.y), []).append(drone)
        survivor_cells: dict[tuple[int, int], list] = {}
        for s in self.world.survivors:
            if not s.rescued:
                survivor_cells.setdefault((s.x, s.y), []).append(s)

        # Sort tiles back-to-front for painter's algorithm
        w = self.world.terrain.width
        h = self.world.terrain.height
        tiles = [(x, y) for y in range(h) for x in range(w)]
        tiles.sort(key=lambda t: self._tile_depth(t[0], t[1]))

        for gx, gy in tiles:
            self._draw_tile(gx, gy, drone_cells, survivor_cells)

        self._draw_hud()

        # Draw recording indicator
        if self._recording:
            pg.draw.circle(self.screen, (255, 30, 30), (self.SCREEN_W - 30, 30), 10)
            rec_lbl = self._font_md.render("REC", True, (255, 30, 30))
            self.screen.blit(rec_lbl, (self.SCREEN_W - 70, 22))

        pg.display.flip()

        # Capture frame for video recording
        self._capture_frame()

        self._frame += 1

    def run(self, tick_world: bool = True, record: bool = False) -> None:
        """Main loop.  When *tick_world* is False the renderer only
        visualises whatever the MCP tools are doing — it never advances
        the simulation clock itself.

        Args:
            tick_world: If True, advance the simulation each frame.
            record: If True, start recording to MP4 immediately.
        """
        if record:
            self.start_recording()

        while self.running:
            if not self.handle_events():
                break
            if tick_world:
                self.world.tick()
            self.render()
            self.clock.tick(self.FPS)

        self.stop_recording()
        self._pygame.quit()

    # ------------------------------------------------------------------
    # Core tile drawing with polygon meshes
    # ------------------------------------------------------------------

    def _draw_tile(self, gx, gy, drone_cells, survivor_cells):
        pg = self._pygame
        terrain = self.world.terrain
        t = terrain.get(gx, gy)
        if t is None:
            return

        # Frustum culling — skip tiles completely off screen
        if not self._tile_on_screen(gx, gy):
            return

        S = _MESH_SUBDIV
        heights = self._heights
        elev = terrain.elevation(gx, gy)
        fog = not self.world.fog_of_war[gy][gx]
        base_elev = self._base_elev
        animated_water = t in (TerrainType.FLOOD_ZONE, TerrainType.COASTLINE)

        # Determine base tile colour
        if fog:
            top_colour = (25, 25, 40)
        else:
            base_colour = _ISO_TERRAIN_TOP.get(t, (150, 150, 150))
            noise_val = self._noise_cache.get((gx, gy), 0.0)
            top_colour = _gradient_shade(base_colour, noise_val, t)

        # --- Draw wall faces (south and east, visibility based on camera) ---
        wall_col_south = self._darken(top_colour, 0.55)
        wall_col_east  = self._darken(top_colour, 0.65)

        if self._south_vis:
            # South wall: bottom edge of tile (gy+1), from gx to gx+1
            svy_wall = (gy + 1) * S
            h_left  = heights.get((gx * S,       svy_wall), float(elev))
            h_right = heights.get(((gx + 1) * S, svy_wall), float(elev))
            top_l = self._project(gx,     gy + 1, h_left)
            top_r = self._project(gx + 1, gy + 1, h_right)
            bot_l = self._project(gx,     gy + 1, base_elev)
            bot_r = self._project(gx + 1, gy + 1, base_elev)
            # Only draw if wall has visible height
            if top_l[1] < bot_l[1] or top_r[1] < bot_r[1]:
                pg.draw.polygon(self.screen, wall_col_south,
                                [top_l, top_r, bot_r, bot_l])

        if self._east_vis:
            # East wall: right edge of tile (gx+1), from gy to gy+1
            svx_wall = (gx + 1) * S
            h_top    = heights.get((svx_wall, gy * S),       float(elev))
            h_bottom = heights.get((svx_wall, (gy + 1) * S), float(elev))
            top_t = self._project(gx + 1, gy,     h_top)
            top_b = self._project(gx + 1, gy + 1, h_bottom)
            bot_t = self._project(gx + 1, gy,     base_elev)
            bot_b = self._project(gx + 1, gy + 1, base_elev)
            if top_t[1] < bot_t[1] or top_b[1] < bot_b[1]:
                pg.draw.polygon(self.screen, wall_col_east,
                                [top_t, top_b, bot_b, bot_t])

        if not self._south_vis:
            # North wall: top edge of tile (gy), from gx to gx+1
            svy_wall = gy * S
            h_left  = heights.get((gx * S,       svy_wall), float(elev))
            h_right = heights.get(((gx + 1) * S, svy_wall), float(elev))
            top_l = self._project(gx,     gy, h_left)
            top_r = self._project(gx + 1, gy, h_right)
            bot_l = self._project(gx,     gy, base_elev)
            bot_r = self._project(gx + 1, gy, base_elev)
            if top_l[1] < bot_l[1] or top_r[1] < bot_r[1]:
                pg.draw.polygon(self.screen, wall_col_south,
                                [top_l, top_r, bot_r, bot_l])

        if not self._east_vis:
            # West wall: left edge of tile (gx), from gy to gy+1
            svx_wall = gx * S
            h_top    = heights.get((svx_wall, gy * S),       float(elev))
            h_bottom = heights.get((svx_wall, (gy + 1) * S), float(elev))
            top_t = self._project(gx, gy,     h_top)
            top_b = self._project(gx, gy + 1, h_bottom)
            bot_t = self._project(gx, gy,     base_elev)
            bot_b = self._project(gx, gy + 1, base_elev)
            if top_t[1] < bot_t[1] or top_b[1] < bot_b[1]:
                pg.draw.polygon(self.screen, wall_col_east,
                                [top_t, top_b, bot_b, bot_t])

        # --- Draw top surface as SUBDIV x SUBDIV mesh of quads ---
        for sy in range(S):
            for sx in range(S):
                svx = gx * S + sx
                svy = gy * S + sy

                # Retrieve the 4 corner heights of this sub-quad
                h00 = heights.get((svx,     svy    ), float(elev))
                h10 = heights.get((svx + 1, svy    ), float(elev))
                h01 = heights.get((svx,     svy + 1), float(elev))
                h11 = heights.get((svx + 1, svy + 1), float(elev))

                # Add per-frame water animation
                if animated_water:
                    phase = self._frame * 0.05
                    anim = math.sin(phase + svx * 0.3 + svy * 0.2) * 0.15
                    h00 += anim
                    h10 += anim
                    h01 += anim
                    h11 += anim

                # Sub-tile world positions (fractional grid coords)
                wx0 = gx + sx       / S
                wx1 = gx + (sx + 1) / S
                wy0 = gy + sy       / S
                wy1 = gy + (sy + 1) / S

                p0 = self._project(wx0, wy0, h00)
                p1 = self._project(wx1, wy0, h10)
                p2 = self._project(wx1, wy1, h11)
                p3 = self._project(wx0, wy1, h01)

                # Per-quad colour with noise variation
                if fog:
                    quad_col = _quad_colour(top_colour, svx, svy)
                else:
                    quad_col = _quad_colour(top_colour, svx, svy)

                pg.draw.polygon(self.screen, quad_col, [p0, p1, p2, p3])

                # Mesh edge lines — thin dark outlines to make the grid visible
                edge_col = self._darken(quad_col, 0.70)
                pg.draw.polygon(self.screen, edge_col, [p0, p1, p2, p3], 1)

        # For fog tiles stop here — no decorations or entities
        if fog:
            return

        # Terrain-specific decorations on top
        self._draw_decorations(gx, gy, elev, t)

        # Entities
        if gx == BASE_X and gy == BASE_Y:
            self._draw_base(gx, gy, elev)
        if (gx, gy) in survivor_cells:
            self._draw_survivors(gx, gy, elev, survivor_cells[(gx, gy)])
        if (gx, gy) in drone_cells:
            self._draw_drones(gx, gy, elev, drone_cells[(gx, gy)])

    # ------------------------------------------------------------------
    # Terrain decorations
    # ------------------------------------------------------------------

    def _draw_decorations(self, gx, gy, elev, terrain_type):
        rng = self._tile_rng(gx, gy)
        z = self.zoom
        noise_val = self._noise_cache.get((gx, gy), 0.0)

        if terrain_type == TerrainType.JUNGLE:
            self._draw_trees(gx, gy, elev, rng, z, noise_val)
        elif terrain_type == TerrainType.FLOOD_ZONE:
            self._draw_water(gx, gy, elev, z)
        elif terrain_type == TerrainType.URBAN_RUBBLE:
            self._draw_rubble(gx, gy, elev, rng, z)
        elif terrain_type == TerrainType.MOUNTAIN:
            self._draw_mountain_peaks(gx, gy, elev, rng, z, noise_val)
        elif terrain_type == TerrainType.VOLCANIC_ASH:
            self._draw_ash(gx, gy, elev, rng, z)
        elif terrain_type == TerrainType.COASTLINE:
            self._draw_sand(gx, gy, elev, rng, z)
        elif terrain_type == TerrainType.CLEAR_ROAD:
            self._draw_road(gx, gy, elev, rng, z)

    def _draw_trees(self, gx, gy, elev, rng, z, noise_val):
        pg = self._pygame
        n = rng.randint(2, 4)
        for _ in range(n):
            ox = rng.uniform(-0.35, 0.35)
            oy = rng.uniform(-0.35, 0.35)
            wx, wy = gx + 0.5 + ox, gy + 0.5 + oy
            trunk_h = rng.uniform(0.25, 0.55)
            shade = rng.uniform(0.65, 1.05)

            bx, by = self._project(wx, wy, elev)
            tx, ty = self._project(wx, wy, elev + trunk_h)
            trunk_w = max(2, int(3 * z))
            pg.draw.line(self.screen, (90, 55, 25), (bx, by), (tx, ty), trunk_w)

            # Irregular canopy polygon — multiple overlapping triangles
            n_layers = rng.randint(2, 3)
            for layer in range(n_layers):
                cz = elev + trunk_h + 0.05 - layer * 0.07
                cx, cy = self._project(wx, wy, cz)
                # Organic canopy: irregular polygon instead of triangle
                n_pts = rng.randint(5, 7)
                canopy_w = (6 + layer * 5) * z
                canopy_h = (9 + layer * 3) * z
                pts = []
                for j in range(n_pts):
                    angle = (j / n_pts) * math.pi * 2
                    r_var = rng.uniform(0.6, 1.0)
                    pts.append((
                        cx + int(math.cos(angle) * canopy_w * r_var),
                        cy + int(math.sin(angle) * canopy_h * 0.5 * r_var) - int(canopy_h * 0.4),
                    ))
                green_var = rng.randint(-25, 25)
                green = self._darken((30 + green_var, 140 + green_var, 30), shade)
                pg.draw.polygon(self.screen, green, pts)
                pg.draw.polygon(self.screen, self._darken(green, 0.7), pts, 1)

    def _draw_water(self, gx, gy, elev, z):
        pg = self._pygame
        cx, cy = self._project(gx + 0.5, gy + 0.5, elev)
        phase = self._frame * 0.05
        noise_val = self._noise_cache.get((gx, gy), 0.0)

        # Multiple animated ellipses for water shimmer
        for i in range(3):
            wave_offset = math.sin(phase + i * 1.8 + noise_val * 2) * 3 * z
            r = int((7 + i * 6 + wave_offset) * z)
            if r <= 0:
                continue
            surf = pg.Surface((r * 2, max(1, r // 2)), pg.SRCALPHA)
            alpha = max(15, 75 - i * 20)
            pg.draw.ellipse(surf, (120, 190, 255, alpha), (0, 0, r * 2, max(1, r // 2)),
                            max(1, int(1.5 * z)))
            self.screen.blit(surf, (cx - r, cy - r // 4))

        # Animated wave lines
        for w_line in range(2):
            offset_y = int((w_line - 0.5) * 5 * z)
            wave_pts = []
            for i in range(7):
                wx2 = gx + 0.1 + i * 0.13
                wy2 = gy + 0.5
                px, py = self._project(wx2, wy2, elev)
                py += offset_y + int(math.sin(phase * 1.2 + i * 0.9 + noise_val) * 2.5 * z)
                wave_pts.append((px, py))
            if len(wave_pts) >= 2:
                alpha_line = 160 if w_line == 0 else 100
                line_surf = pg.Surface((self.SCREEN_W, self.SCREEN_H), pg.SRCALPHA)
                pg.draw.lines(line_surf, (160, 215, 255, alpha_line), False, wave_pts,
                              max(1, int(z)))
                self.screen.blit(line_surf, (0, 0))

    def _draw_rubble(self, gx, gy, elev, rng, z):
        pg = self._pygame
        # Draw irregular polygon chunks of rubble
        n_chunks = rng.randint(3, 6)
        for _ in range(n_chunks):
            ox = rng.uniform(-0.3, 0.3)
            oy = rng.uniform(-0.3, 0.3)
            sx, sy = self._project(gx + 0.5 + ox, gy + 0.5 + oy, elev)
            # Irregular polygon for each rubble chunk
            n_v = rng.randint(4, 6)
            bw = int(rng.randint(4, 9) * z)
            bh = int(rng.randint(3, 7) * z)
            pts = []
            for j in range(n_v):
                angle = (j / n_v) * math.pi * 2
                r_x = bw * rng.uniform(0.5, 1.0)
                r_y = bh * rng.uniform(0.5, 1.0)
                pts.append((sx + int(math.cos(angle) * r_x),
                             sy + int(math.sin(angle) * r_y)))
            grey = rng.randint(85, 145)
            reddish = rng.randint(-10, 10)
            pg.draw.polygon(self.screen, (grey + reddish, grey - 5, grey - 15), pts)
            # Crack line on larger chunks
            if bw > int(5 * z) and rng.random() > 0.4:
                crack_a = (sx + rng.randint(-bw // 2, bw // 2),
                           sy + rng.randint(-bh // 2, bh // 2))
                crack_b = (sx + rng.randint(-bw // 2, bw // 2),
                           sy + rng.randint(-bh // 2, bh // 2))
                pg.draw.line(self.screen, (60, 45, 35), crack_a, crack_b, 1)

    def _draw_mountain_peaks(self, gx, gy, elev, rng, z, noise_val):
        pg = self._pygame
        n_rocks = rng.randint(2, 4)
        for _ in range(n_rocks):
            ox = rng.uniform(-0.25, 0.25)
            oy = rng.uniform(-0.25, 0.25)
            sx, sy = self._project(gx + 0.5 + ox, gy + 0.5 + oy, elev)
            r = int(rng.randint(4, 8) * z)
            n_v = rng.randint(5, 8)
            # Jagged peak polygon
            pts = []
            for j in range(n_v):
                angle = (j / n_v) * math.pi * 2
                # Alternate between far and near to create jagged silhouette
                if j % 2 == 0:
                    dist = r * rng.uniform(0.85, 1.0)
                else:
                    dist = r * rng.uniform(0.4, 0.65)
                pts.append((sx + int(math.cos(angle) * dist),
                             sy + int(math.sin(angle) * dist * 0.55)))
            grey = rng.randint(110, 170)
            pg.draw.polygon(self.screen, (grey, grey, grey + 15), pts)
            pg.draw.polygon(self.screen, (grey - 35, grey - 35, grey - 25), pts, 1)
            # Snow cap on high noise value (elevated peaks)
            if noise_val > 0.1 and rng.random() > 0.5:
                snow_pts = pts[:max(3, len(pts) // 2)]
                pg.draw.polygon(self.screen, (230, 240, 255), snow_pts)

    def _draw_ash(self, gx, gy, elev, rng, z):
        pg = self._pygame
        # Irregular ash deposit clusters
        for _ in range(rng.randint(6, 12)):
            ox = rng.uniform(-0.35, 0.35)
            oy = rng.uniform(-0.35, 0.35)
            sx, sy = self._project(gx + 0.5 + ox, gy + 0.5 + oy, elev)
            # Small irregular polygon for each ash deposit
            n_v = rng.randint(4, 6)
            r_ash = int(rng.uniform(1.5, 3.5) * z)
            pts = []
            for j in range(n_v):
                angle = (j / n_v) * math.pi * 2
                dist = r_ash * rng.uniform(0.6, 1.0)
                pts.append((sx + int(math.cos(angle) * dist),
                             sy + int(math.sin(angle) * dist * 0.7)))
            c = rng.randint(45, 95)
            if len(pts) >= 3:
                pg.draw.polygon(self.screen, (c, c - 8, c - 18), pts)

        # Animated smoke plume
        smoke_offset = rng.random() * 10
        smoke_phase = self._frame * 0.04 + smoke_offset
        smx, smy = self._project(gx + 0.5, gy + 0.5, elev + 0.25)
        smx += int(math.sin(smoke_phase) * 5 * z)
        n_puffs = 4
        for i in range(n_puffs):
            puff_w = int((7 + i * 3) * z)
            puff_h = int((5 + i * 2) * z)
            a = max(5, 55 - i * 12)
            puff_surf = pg.Surface((puff_w, puff_h), pg.SRCALPHA)
            # Irregular puff polygon
            n_pv = 6
            puff_pts = []
            for j in range(n_pv):
                angle = (j / n_pv) * math.pi * 2
                dist_x = (puff_w // 2) * rng.uniform(0.6, 1.0)
                dist_y = (puff_h // 2) * rng.uniform(0.6, 1.0)
                puff_pts.append((puff_w // 2 + int(math.cos(angle) * dist_x),
                                  puff_h // 2 + int(math.sin(angle) * dist_y)))
            if len(puff_pts) >= 3:
                pg.draw.polygon(puff_surf, (130, 115, 105, a), puff_pts)
            self.screen.blit(puff_surf, (smx - puff_w // 2 + int(i * 1.5 * z),
                                          smy - int(i * 6 * z)))

    def _draw_sand(self, gx, gy, elev, rng, z):
        pg = self._pygame
        # Irregular sand grain clusters
        for _ in range(rng.randint(5, 10)):
            ox = rng.uniform(-0.35, 0.35)
            oy = rng.uniform(-0.35, 0.35)
            sx, sy = self._project(gx + 0.5 + ox, gy + 0.5 + oy, elev)
            c = rng.randint(185, 225)
            r_grain = max(1, int(rng.uniform(1.0, 2.5) * z))
            n_v = rng.randint(4, 6)
            pts = []
            for j in range(n_v):
                angle = (j / n_v) * math.pi * 2
                dist = r_grain * rng.uniform(0.7, 1.0)
                pts.append((sx + int(math.cos(angle) * dist),
                             sy + int(math.sin(angle) * dist * 0.6)))
            if len(pts) >= 3:
                pg.draw.polygon(self.screen, (c, c - 8, c - 25), pts)

        # Animated shoreline polygon wave
        phase = self._frame * 0.06
        noise_val = self._noise_cache.get((gx, gy), 0.0)
        wave_pts = []
        for i in range(8):
            wx2 = gx + 0.1 + i * 0.11
            wy2 = gy + 0.65
            px, py = self._project(wx2, wy2, elev)
            py += int(math.sin(phase + i * 1.1 + noise_val * 2) * 2.5 * z)
            wave_pts.append((px, py))
        if len(wave_pts) >= 2:
            pg.draw.lines(self.screen, (110, 170, 210), False, wave_pts,
                          max(1, int(z)))

    def _draw_road(self, gx, gy, elev, rng, z):
        pg = self._pygame
        if rng.random() > 0.4:
            # Road marking dashes — aligned with tile direction
            for i in range(3):
                wx = gx + 0.28 + i * 0.16
                sx, sy = self._project(wx, gy + 0.5, elev)
                ex, ey = self._project(wx + 0.08, gy + 0.5, elev)
                pg.draw.line(self.screen, (165, 170, 155), (sx, sy), (ex, ey),
                             max(1, int(z)))

    # ------------------------------------------------------------------
    # Entity drawing
    # ------------------------------------------------------------------

    def _draw_base(self, gx, gy, elev):
        pg = self._pygame
        cx, cy = self._project(gx + 0.5, gy + 0.5, elev + 0.3)
        r = max(6, int(10 * self.zoom))
        pg.draw.circle(self.screen, (0, 200, 80), (cx, cy), r)
        pg.draw.circle(self.screen, (200, 255, 200), (cx, cy), r, 2)
        label = self._font_sm.render("BASE", True, (255, 255, 255))
        self.screen.blit(label, (cx - label.get_width() // 2, cy - r - 14))

    def _draw_survivors(self, gx, gy, elev, survivors):
        pg = self._pygame
        for i, s in enumerate(survivors):
            speed = _URGENCY_PULSE.get(s.urgency, 1.0)
            phase = (self._frame * speed * 2 * math.pi) / self.FPS
            pulse = (math.sin(phase) + 1.0) / 2.0
            r_base = max(5, int(7 * self.zoom))
            r = int(r_base + pulse * r_base * 0.6)

            # Spread multiple survivors
            spread = (i - len(survivors) // 2) * 0.15
            cx, cy = self._project(gx + 0.5 + spread, gy + 0.5, elev + 0.2)

            # Body trapezoid
            bw = int(r * 0.8)
            bh = int(r * 0.6)
            body = [(cx - bw // 2, cy + r_base // 2),
                    (cx + bw // 2, cy + r_base // 2),
                    (cx + bw // 3, cy + r_base // 2 + bh),
                    (cx - bw // 3, cy + r_base // 2 + bh)]
            pg.draw.polygon(self.screen, (200, 30, 30), body)
            # Head
            pg.draw.circle(self.screen, (220, 30, 30), (cx, cy), max(3, r // 2))
            pg.draw.circle(self.screen, (255, 150, 150), (cx, cy), max(2, r // 4))
            # Critical exclamation
            if s.urgency == "critical":
                exc = self._font_md.render("!", True, (255, 50, 50))
                self.screen.blit(exc, (cx - exc.get_width() // 2,
                                       cy - r - int(14 * self.zoom)))

    def _draw_drones(self, gx, gy, elev, drones):
        pg = self._pygame
        n = len(drones)
        for i, d in enumerate(drones):
            colour = _OFFLINE_COLOUR if d.status == DroneStatus.OFFLINE else \
                     _ROLE_COLOURS.get(d.role, (200, 200, 200))

            is_leader = d.is_leader
            r_base = max(6, int(8 * self.zoom))
            r = int(r_base * 1.4) if is_leader else r_base

            spread = (i - n // 2) * 0.2
            bob = 0
            if d.status == DroneStatus.ACTIVE:
                bob = math.sin(self._frame * 0.2 + i) * 0.1

            cx, cy = self._project(gx + 0.5 + spread, gy + 0.5, elev + 0.5 + bob)

            # Shadow
            shx, shy = self._project(gx + 0.5 + spread, gy + 0.5, elev)
            pg.draw.ellipse(self.screen, (0, 0, 0),
                            (shx - r, shy - r // 3, r * 2, int(r * 0.7)))

            # Body
            pg.draw.circle(self.screen, colour, (cx, cy), r)

            # Rotor lines
            rotor_angle = self._frame * 0.3 + i
            rotor_len = int(r * 1.3)
            for a in range(4):
                angle = rotor_angle + a * (math.pi / 2)
                rx = cx + int(math.cos(angle) * rotor_len)
                ry = cy + int(math.sin(angle) * rotor_len * 0.5)
                pg.draw.line(self.screen, self._darken(colour, 0.7),
                             (cx, cy), (rx, ry), max(1, int(self.zoom)))
                pg.draw.circle(self.screen, (200, 200, 200), (rx, ry),
                               max(1, int(2 * self.zoom)))

            # Leader golden ring
            if is_leader:
                glow = (math.sin(self._frame * 0.1) + 1) / 2
                pg.draw.circle(self.screen, (255, 215, 0), (cx, cy),
                               r + 2 + int(glow * 3), 2)
            else:
                pg.draw.circle(self.screen, self._darken(colour, 1.3), (cx, cy), r, 1)

            # Label
            short_id = d.drone_id.split("-")[-1] if "-" in d.drone_id else d.drone_id[:2]
            lbl = self._font_sm.render(short_id, True, (240, 240, 240))
            self.screen.blit(lbl, (cx - lbl.get_width() // 2, cy - r - 13))

    # ------------------------------------------------------------------
    # HUD
    # ------------------------------------------------------------------

    def _draw_hud(self):
        self._draw_hud_topbar()
        self._draw_hud_drone_panel()
        self._draw_hud_legend()

    def _draw_hud_topbar(self):
        pg = self._pygame
        w = self.world
        pad = self._HUD_PAD
        angle_deg = math.degrees(self.cam_angle) % 360

        lines = [
            f"Tick:     {w.tick_count:04d} / 500",
            f"Coverage: {w._calc_coverage():.1f}%",
            f"Rescued:  {w.rescued_count} / {len(w.survivors)}",
            f"Angle:    {angle_deg:.0f}\u00b0",
        ]

        bg_rect = pg.Rect(pad, pad, 220, len(lines) * 20 + pad * 2)
        s = pg.Surface((bg_rect.width, bg_rect.height), pg.SRCALPHA)
        s.fill((0, 0, 0, 160))
        self.screen.blit(s, (bg_rect.x, bg_rect.y))
        for i, line in enumerate(lines):
            surf = self._font_md.render(line, True, (200, 230, 200))
            self.screen.blit(surf, (pad * 2, pad * 2 + i * 20))

    def _draw_hud_drone_panel(self):
        pg = self._pygame
        panel_w = self._HUD_PANEL_W
        pad = self._HUD_PAD
        x0 = self.SCREEN_W - panel_w - pad
        y0 = pad

        drones = list(self.world.drones.values())
        row_h = 56
        panel_h = len(drones) * row_h + pad * 2 + 20

        s = pg.Surface((panel_w, panel_h), pg.SRCALPHA)
        s.fill((0, 0, 0, 170))
        self.screen.blit(s, (x0, y0))

        title = self._font_md.render("FLEET STATUS", True, (180, 220, 255))
        self.screen.blit(title, (x0 + pad, y0 + pad))

        for i, drone in enumerate(drones):
            ry = y0 + pad + 20 + i * row_h
            leader_tag = " *" if drone.is_leader else ""
            id_text = self._font_md.render(f"{drone.drone_id}{leader_tag}", True, (240, 240, 240))
            self.screen.blit(id_text, (x0 + pad, ry))

            role_col = (180, 60, 60) if drone.status == DroneStatus.OFFLINE else \
                       _ROLE_COLOURS.get(drone.role, (200, 200, 200))
            role_text = self._font_sm.render(
                f"{drone.role.value.upper()}  {drone.status.value}", True, role_col)
            self.screen.blit(role_text, (x0 + pad, ry + 16))

            bar_x, bar_y = x0 + pad, ry + 30
            bar_w, bar_h = panel_w - pad * 2, 10
            pg.draw.rect(self.screen, (60, 60, 60), (bar_x, bar_y, bar_w, bar_h))
            fill_w = int(bar_w * drone.battery / 100.0)
            bar_colour = (60, 200, 60) if drone.battery > 50 else \
                         (220, 200, 40) if drone.battery > 20 else (220, 60, 40)
            if fill_w > 0:
                pg.draw.rect(self.screen, bar_colour, (bar_x, bar_y, fill_w, bar_h))
            pg.draw.rect(self.screen, (120, 120, 120), (bar_x, bar_y, bar_w, bar_h), 1)
            bat_lbl = self._font_sm.render(f"{drone.battery:.0f}%", True, (200, 200, 200))
            self.screen.blit(bat_lbl, (bar_x + bar_w + 3, bar_y - 1))

    def _draw_hud_legend(self):
        pg = self._pygame
        pad = self._HUD_PAD
        y0 = self.SCREEN_H - 60

        items = [
            ((0, 200, 80),   "Base"),
            ((0, 255, 255),  "Scout"),
            ((255, 50, 50),  "Rescue"),
            ((255, 230, 0),  "Relay"),
            ((230, 230, 230),"Idle"),
            ((220, 30, 30),  "Survivor"),
            ((80, 80, 80),   "Offline"),
        ]

        bg_w = len(items) * 110 + pad * 2
        s = pg.Surface((bg_w, 50), pg.SRCALPHA)
        s.fill((0, 0, 0, 160))
        self.screen.blit(s, (pad, y0))

        for j, (colour, label) in enumerate(items):
            cx = pad * 2 + j * 110 + 8
            cy = y0 + 25
            pg.draw.circle(self.screen, colour, (cx, cy), 7)
            lbl = self._font_sm.render(label, True, (220, 220, 220))
            self.screen.blit(lbl, (cx + 12, cy - 7))

        hint = self._font_sm.render(
            "WASD: pan  Scroll: zoom  Right-drag/Q/E: rotate  G: grid  R: reset  F9: record",
            True, (140, 140, 160),
        )
        self.screen.blit(hint, (pad, self.SCREEN_H - 16))


# ===========================================================================
# Convenience function
# ===========================================================================

def render(world: "World", use_colour: bool = True) -> None:
    TerminalRenderer(use_colour=use_colour).render(world)


# ===========================================================================
# Standalone demo
# ===========================================================================

if __name__ == "__main__":
    import pathlib
    base_dir = pathlib.Path(__file__).resolve().parent.parent
    terrain_path = str(base_dir / "terrain_data" / "sulawesi_earthquake.json")

    from simulation.world import World
    world = World(terrain_path=terrain_path, num_drones=5, num_survivors=8)
    renderer = IsometricRenderer(world)
    renderer.run()
