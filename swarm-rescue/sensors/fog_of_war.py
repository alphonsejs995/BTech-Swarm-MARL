# sensors/fog_of_war.py
# FogOfWar class — tracks which cells have been revealed by drone scans.
#
# Design note: world.py accesses fog_of_war as a 2D list (world.fog_of_war[y][x]).
# FogOfWar supports __getitem__ / __setitem__ via row proxies so existing world.py
# code works without modification, while also providing .reveal(), .coverage_percent(),
# .unrevealed_sectors(), and .is_revealed() for the MCP server and sensors layer.


class _Row:
    """Proxy for a single row of the FogOfWar grid.

    Returned by FogOfWar.__getitem__(y) so that world.py can do:
        world.fog_of_war[y][x] = True
    and iteration like:
        for cell in row: ...
    """

    def __init__(self, data: list):
        # data is the actual list[bool] stored in FogOfWar.grid[y]
        self._data = data

    def __getitem__(self, x: int) -> bool:
        return self._data[x]

    def __setitem__(self, x: int, value: bool) -> None:
        self._data[x] = value

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class FogOfWar:
    """Tracks which cells have been revealed by drone scans.

    The underlying store is ``grid``: a 2-D list[list[bool]] where
    False means hidden and True means revealed.

    Indexing support
    ----------------
    ``fog[y]`` returns a _Row proxy that forwards ``__getitem__`` and
    ``__setitem__`` to the underlying list, so ``world.py`` can continue
    to write ``self.fog_of_war[ny][nx] = True`` without any changes.

    Method interface
    ----------------
    Used by the MCP server tools::

        coverage  = world.fog_of_war.coverage_percent()
        sectors   = world.fog_of_war.unrevealed_sectors()
        revealed  = world.fog_of_war.reveal(cx, cy, radius)
        is_seen   = world.fog_of_war.is_revealed(x, y)
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        # Internal 2-D grid: grid[row][col] i.e. grid[y][x]
        self.grid: list[list[bool]] = [
            [False] * width for _ in range(height)
        ]
        # Pre-build proxy rows so __getitem__ is O(1) without extra alloc
        self._rows: list[_Row] = [_Row(self.grid[y]) for y in range(height)]

    # ------------------------------------------------------------------
    # 2-D list compatibility (world.py uses fog_of_war[y][x])
    # ------------------------------------------------------------------

    def __getitem__(self, y: int) -> _Row:
        return self._rows[y]

    def __iter__(self):
        """Support 'for row in fog_of_war' as used in world._calc_coverage."""
        return iter(self._rows)

    def __len__(self) -> int:
        return self.height

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def reveal(self, cx: int, cy: int, radius: int) -> list:
        """Reveal all cells within a circular radius of (cx, cy).

        Returns a list of [x, y] pairs for cells that were newly revealed
        (previously hidden). Already-revealed cells are not included.
        """
        newly_revealed = []
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy > radius * radius:
                    continue
                x, y = cx + dx, cy + dy
                if not (0 <= x < self.width and 0 <= y < self.height):
                    continue
                if not self.grid[y][x]:
                    self.grid[y][x] = True
                    newly_revealed.append([x, y])
        return newly_revealed

    def is_revealed(self, x: int, y: int) -> bool:
        """Return True if the cell at (x, y) has been revealed."""
        if 0 <= x < self.width and 0 <= y < self.height:
            return self.grid[y][x]
        return False

    def coverage_percent(self) -> float:
        """Return the percentage of total map cells that have been revealed."""
        revealed = sum(1 for row in self.grid for cell in row if cell)
        total = self.width * self.height
        if total == 0:
            return 0.0
        return round(revealed / total * 100, 1)

    def unrevealed_sectors(self, sector_size: int = 5) -> list:
        """Group the grid into (sector_size x sector_size) blocks.

        Returns a list of sector dicts for sectors where less than 50% of
        cells have been revealed. Each entry contains:
            - "sector_origin": [sx, sy]  — top-left corner of the sector
            - "coverage": float          — percentage of cells revealed (0-100)
            - "center": [cx, cy]         — approximate center of the sector
              (useful for the agent when deciding where to send scouts)

        Sectors where all cells are revealed are excluded.
        """
        sectors = []
        for sy in range(0, self.height, sector_size):
            for sx in range(0, self.width, sector_size):
                total = 0
                revealed = 0
                for dy in range(min(sector_size, self.height - sy)):
                    for dx in range(min(sector_size, self.width - sx)):
                        total += 1
                        if self.grid[sy + dy][sx + dx]:
                            revealed += 1
                if total > 0 and revealed / total < 0.5:
                    # Compute approximate centre of this sector
                    actual_w = min(sector_size, self.width - sx)
                    actual_h = min(sector_size, self.height - sy)
                    center_x = sx + actual_w // 2
                    center_y = sy + actual_h // 2
                    sectors.append({
                        "sector_origin": [sx, sy],
                        "coverage": round(revealed / total * 100, 1),
                        "center": [center_x, center_y],
                    })
        return sectors
