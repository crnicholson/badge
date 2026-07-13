"""
badge_simulator.py
====================

Run GitHub Badger 2350 games locally via Pygame by simulating the
`badgeware` API (screen, io, brushes, shapes, Image/SpriteSheet).
"""

import argparse
import importlib.util
import json
import math
import os
import platform
import struct
import sys
import traceback
from types import ModuleType

try:
    import pygame  # type: ignore
except ImportError:
    raise SystemExit(
        "Pygame is required to run the local simulator. Install with: pip install pygame"
    )

# -----------------------------------------------------------------------------
# Virtual “/system” mapping (NO filesystem changes)
# -----------------------------------------------------------------------------

SIM_ROOT = None


def _find_sim_root(start_dir: str) -> str:
    """Walk upward to find a directory that contains 'apps'. Fallback to start_dir."""
    cur = os.path.abspath(start_dir)
    for _ in range(8):
        if os.path.isdir(os.path.join(cur, "apps")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.abspath(start_dir)


def map_system_path(p: str) -> str:
    """Map '/system/...' paths to SIM_ROOT and root files to temp directory."""
    global SIM_ROOT
    if SIM_ROOT is None:
        SIM_ROOT = _find_sim_root(os.getcwd())
    if p.startswith("/system"):
        tail = p[len("/system"):].lstrip("/\\")
        return os.path.join(SIM_ROOT, tail) if tail else SIM_ROOT
    # Map root-level files (e.g., /avatar.png) to writable temp directory
    elif p.startswith("/") and not p.startswith("//"):
        tail = p[1:]  # Remove leading /
        # Only map simple filenames (no subdirectories)
        if "/" not in tail and "\\" not in tail and tail != "":
            import tempfile
            root_dir = os.path.join(tempfile.gettempdir(), "badge_simulator_root")
            os.makedirs(root_dir, exist_ok=True)
            return os.path.join(root_dir, tail)
    return p


# Intercept os.chdir so games can safely do os.chdir("/system/apps/foo")
_real_chdir = os.chdir


def _safe_chdir(path: str):
    _real_chdir(map_system_path(path))


os.chdir = _safe_chdir  # type: ignore

# Intercept open() to map /system and badge root paths
_real_open = open

def _safe_open(file, mode='r', *args, **kwargs):
    if isinstance(file, (str, bytes, os.PathLike)):
        fs_path = os.fspath(file)
        if isinstance(fs_path, str):
            # Map /system paths to badge directory
            if fs_path.startswith("/system"):
                file = map_system_path(fs_path)
            # Map root-level files (e.g., /avatar.png) to writable temp directory
            elif fs_path.startswith("/") and not fs_path.startswith("//"):
                tail = fs_path[1:]  # Remove leading /
                # Only map simple filenames (no subdirectories)
                # This avoids mapping system paths like /Users/... or /var/...
                if "/" not in tail and "\\" not in tail and tail != "":
                    import tempfile
                    root_dir = os.path.join(tempfile.gettempdir(), "badge_simulator_root")
                    os.makedirs(root_dir, exist_ok=True)
                    file = os.path.join(root_dir, tail)
    return _real_open(file, mode, *args, **kwargs)

import builtins
builtins.open = _safe_open  # type: ignore

# Allow `os.listdir("/system/...")`
_real_listdir = os.listdir


def _safe_listdir(path="."):
    if isinstance(path, (str, bytes, os.PathLike)):
        fs_path = os.fspath(path)
        if isinstance(fs_path, str):
            return _real_listdir(map_system_path(fs_path))
        return _real_listdir(fs_path)
    return _real_listdir(path)


os.listdir = _safe_listdir  # type: ignore

# Intercept os.remove so games can remove files from root
_real_remove = os.remove

def _safe_remove(path):
    if isinstance(path, (str, bytes, os.PathLike)):
        fs_path = os.fspath(path)
        if isinstance(fs_path, str):
            if fs_path.startswith("/system"):
                return _real_remove(map_system_path(fs_path))
            elif fs_path.startswith("/") and not fs_path.startswith("//"):
                tail = fs_path[1:]
                if "/" not in tail and "\\" not in tail and tail != "":
                    import tempfile
                    root_dir = os.path.join(tempfile.gettempdir(), "badge_simulator_root")
                    return _real_remove(os.path.join(root_dir, tail))
        return _real_remove(fs_path)
    return _real_remove(path)

os.remove = _safe_remove  # type: ignore

# Intercept sys.path operations to map "/" to SIM_ROOT
class _SafePathList(list):
    """Wrapper for sys.path that maps "/" to SIM_ROOT when inserted."""
    
    def __init__(self, original_path):
        super().__init__(original_path)
        self._original = original_path
    
    def insert(self, index, item):
        if item == "/":
            # Map "/" to SIM_ROOT for secrets.py imports
            global SIM_ROOT
            if SIM_ROOT is None:
                SIM_ROOT = _find_sim_root(os.getcwd())
            item = SIM_ROOT
        super().insert(index, item)
    
    def append(self, item):
        if item == "/":
            # Map "/" to SIM_ROOT for secrets.py imports
            global SIM_ROOT
            if SIM_ROOT is None:
                SIM_ROOT = _find_sim_root(os.getcwd())
            item = SIM_ROOT
        super().append(item)

# Replace sys.path with our safe wrapper
sys.path = _SafePathList(sys.path)

# -----------------------------------------------------------------------------
# Badgeware API stubs
# -----------------------------------------------------------------------------

class _Shape:
    """Base shape that supports optional affine transforms."""

    __slots__ = ("transform",)

    def __init__(self) -> None:
        self.transform = None  # optional Matrix

    def points(self):
        raise NotImplementedError

    def stroke(self, width: float):
        return _StrokedShape(self, width)


class _StrokedShape(_Shape):
    __slots__ = ("shape", "width")

    def __init__(self, shape: _Shape, width: float) -> None:
        super().__init__()
        self.shape = shape
        self.width = max(0.0, float(width))
        if getattr(shape, "transform", None) is not None:
            self.transform = shape.transform


class _Rectangle(_Shape):
    __slots__ = ("x", "y", "w", "h")

    def __init__(self, x: float, y: float, w: float, h: float) -> None:
        super().__init__()
        self.x, self.y, self.w, self.h = x, y, w, h

    def points(self):
        x, y, w, h = self.x, self.y, self.w, self.h
        return [
            (x, y),
            (x + w, y),
            (x + w, y + h),
            (x, y + h),
        ]


class _RoundedRectangle(_Rectangle):
    __slots__ = ("radii",)

    def __init__(self, x: float, y: float, w: float, h: float, radius: float, *corner_radii) -> None:
        super().__init__(x, y, w, h)
        if corner_radii:
            radii = list(corner_radii[:4])
            if len(radii) < 4:
                radii.extend([radius] * (4 - len(radii)))
        else:
            radii = [radius] * 4
        self.radii = [max(0.0, float(r)) for r in radii]

    def points(self):
        x, y, w, h = self.x, self.y, self.w, self.h
        radii = [
            min(r, w / 2.0, h / 2.0) if r > 0 else 0.0
            for r in self.radii
        ]

        corner_points = [
            (x, y),
            (x + w, y),
            (x + w, y + h),
            (x, y + h),
        ]

        corners = [
            (x + radii[0], y + radii[0], 180, 270, radii[0]),                 # top-left
            (x + w - radii[1], y + radii[1], 270, 360, radii[1]),             # top-right
            (x + w - radii[2], y + h - radii[2], 0, 90, radii[2]),            # bottom-right
            (x + radii[3], y + h - radii[3], 90, 180, radii[3]),              # bottom-left
        ]

        points = []
        for idx, (cx, cy, start_deg, end_deg, radius) in enumerate(corners):
            if radius <= 0:
                pt = corner_points[idx]
                if points and points[-1] == pt:
                    continue
                points.append(pt)
                continue

            segments = max(4, int(radius * 2))
            for step in range(segments + 1):
                if idx > 0 and step == 0:
                    continue
                t = step / segments
                angle = math.radians(start_deg + (end_deg - start_deg) * t)
                px = cx + radius * math.cos(angle)
                py = cy + radius * math.sin(angle)
                points.append((px, py))
        return points


class _Circle(_Shape):
    __slots__ = ("x", "y", "radius", "segments")

    def __init__(self, x: float, y: float, radius: float, segments: int = 32) -> None:
        super().__init__()
        self.x, self.y, self.radius = x, y, radius
        self.segments = max(12, int(segments))

    def points(self):
        pts = []
        for i in range(self.segments):
            theta = (2.0 * math.pi * i) / self.segments
            pts.append(
                (
                    self.x + self.radius * math.cos(theta),
                    self.y + self.radius * math.sin(theta),
                )
            )
        return pts


class _Squircle(_Shape):
    __slots__ = ("x", "y", "radius", "n", "segments")

    def __init__(self, x: float, y: float, radius: float, n: float = 4.0, segments: int = 64) -> None:
        super().__init__()
        self.x, self.y, self.radius = x, y, radius
        self.n = n if n else 4.0
        self.segments = max(24, int(segments))

    def points(self):
        pts = []
        exponent = 2.0 / max(1e-3, float(self.n))
        for i in range(self.segments):
            theta = (2.0 * math.pi * i) / self.segments
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            px = self.radius * math.copysign(abs(cos_t) ** exponent, cos_t)
            py = self.radius * math.copysign(abs(sin_t) ** exponent, sin_t)
            pts.append((self.x + px, self.y + py))
        return pts


class _Line(_Shape):
    __slots__ = ("x1", "y1", "x2", "y2", "thickness")

    def __init__(self, x1: float, y1: float, x2: float, y2: float, thickness: float = 1.0) -> None:
        super().__init__()
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self.thickness = thickness


class _RegularPolygon(_Shape):
    __slots__ = ("x", "y", "radius", "sides")

    def __init__(self, x: float, y: float, radius: float, sides: int) -> None:
        super().__init__()
        self.x, self.y, self.radius = x, y, radius
        self.sides = max(3, int(sides))

    def points(self):
        pts = []
        for i in range(self.sides):
            angle_deg = (360.0 / self.sides) * i
            angle = math.radians(angle_deg)
            px = self.x + self.radius * math.sin(angle)
            py = self.y + self.radius * math.cos(angle)
            pts.append((px, py))
        return pts


class _Arc(_Shape):
    __slots__ = ("x", "y", "radius", "start_deg", "end_deg", "thickness")

    def __init__(self, x: float, y: float, radius: float, start_deg: float, end_deg: float, thickness: float = 1.0) -> None:
        super().__init__()
        self.x, self.y, self.radius = x, y, radius
        self.start_deg = float(start_deg)
        self.end_deg = float(end_deg)
        self.thickness = max(1.0, float(thickness))

    def points(self):
        start = self.start_deg
        end = self.end_deg
        if end < start:
            end += 360.0
        span = max(0.0, end - start)
        segments = max(8, int(self.radius * max(1.0, span / 45.0)))
        pts = []
        for i in range(segments + 1):
            t = i / segments if segments else 0.0
            angle = math.radians(start + span * t)
            px = self.x + self.radius * math.sin(angle)
            py = self.y + self.radius * math.cos(angle)
            pts.append((px, py))
        return pts

    def stroke(self, width: float):
        stroked = _Arc(self.x, self.y, self.radius, self.start_deg, self.end_deg, width)
        if getattr(self, "transform", None) is not None:
            stroked.transform = self.transform
        return stroked


class _Pie(_Arc):
    __slots__ = ()

    def __init__(self, x: float, y: float, radius: float, start_deg: float, end_deg: float) -> None:
        super().__init__(x, y, radius, start_deg, end_deg, thickness=1.0)

    def points(self):
        pts = super().points()
        if pts:
            return [(self.x, self.y)] + pts
        return [(self.x, self.y)]


def _round_points(points):
    return [(int(round(px)), int(round(py))) for px, py in points]


def _render_shape(surface, color, shape, transform=None, offset=(0.0, 0.0)):
    base_shape = shape
    stroke_width = None

    if isinstance(shape, _StrokedShape):
        base_shape = shape.shape
        stroke_width = shape.width
        if transform is None:
            transform = getattr(shape, "transform", None)

    if transform is None:
        transform = getattr(base_shape, "transform", None)

    ox, oy = offset

    if isinstance(base_shape, _Line):
        x1, y1 = base_shape.x1, base_shape.y1
        x2, y2 = base_shape.x2, base_shape.y2
        if isinstance(transform, Matrix):
            x1, y1 = transform.transformed_point(x1, y1)
            x2, y2 = transform.transformed_point(x2, y2)
        width = stroke_width if stroke_width is not None else base_shape.thickness
        pygame.draw.line(
            surface,
            color,
            (int(round(x1 + ox)), int(round(y1 + oy))),
            (int(round(x2 + ox)), int(round(y2 + oy))),
            max(1, int(round(width))),
        )
        return

    if isinstance(base_shape, _Pie):
        points = base_shape.points()
        if isinstance(transform, Matrix):
            points = [transform.transformed_point(px, py) for px, py in points]
        points = [(px + ox, py + oy) for px, py in points]
        if not points:
            return
        if stroke_width is not None and stroke_width > 0:
            pygame.draw.polygon(
                surface,
                color,
                _round_points(points),
                max(1, int(round(stroke_width))),
            )
        else:
            pygame.draw.polygon(surface, color, _round_points(points))
        return

    if isinstance(base_shape, _Arc):
        points = base_shape.points()
        if isinstance(transform, Matrix):
            points = [transform.transformed_point(px, py) for px, py in points]
        points = [(px + ox, py + oy) for px, py in points]
        if len(points) >= 2:
            width = stroke_width if stroke_width is not None else base_shape.thickness
            pygame.draw.lines(
                surface,
                color,
                False,
                _round_points(points),
                max(1, int(round(width))),
            )
        return

    if not hasattr(base_shape, "points"):
        return

    points = list(base_shape.points())
    if not points:
        return

    if isinstance(transform, Matrix):
        points = [transform.transformed_point(px, py) for px, py in points]
    points = [(px + ox, py + oy) for px, py in points]

    if stroke_width is not None and stroke_width > 0:
        pygame.draw.polygon(
            surface,
            color,
            _round_points(points),
            max(1, int(round(stroke_width))),
        )
    else:
        pygame.draw.polygon(surface, color, _round_points(points))


class _SurfaceTarget:
    __slots__ = ("_surface", "brush", "font", "antialias")

    def __init__(self, surface: pygame.Surface):
        self._surface = surface
        self.brush = brushes.color(255, 255, 255)
        self.font = pygame.font.Font(None, 14)
        self.antialias = 0

    def _norm_color(self, c):
        if c is None:
            return (0, 0, 0, 255)
        if isinstance(c, int):
            return (c, c, c, 255)
        return c

    def _unwrap(self, image):
        return image._surface if isinstance(image, Image) else image

    def clear(self, color=None) -> None:
        fill_color = self._norm_color(color if color is not None else self.brush)
        self._surface.fill(fill_color)

    def draw(self, shape: _Shape) -> None:
        color = self._norm_color(self.brush)
        _render_shape(self._surface, color, shape)

    def blit(self, image, x: float, y: float, transform: "Matrix" = None) -> None:
        if isinstance(transform, Matrix):
            x, y = transform.transformed_point(x, y)
        self._surface.blit(self._unwrap(image), (int(round(x)), int(round(y))))

    def scale_blit(self, image, x: float, y: float, w: int, h: int, transform: "Matrix" = None) -> None:
        if isinstance(transform, Matrix):
            x, y = transform.transformed_point(x, y)
        src = self._unwrap(image)
        new_w = max(1, abs(w))
        new_h = max(1, abs(h))
        scaled = pygame.transform.scale(src, (new_w, new_h))
        if w < 0:
            scaled = pygame.transform.flip(scaled, True, False)
        if h < 0:
            scaled = pygame.transform.flip(scaled, False, True)
        self._surface.blit(scaled, (int(round(x)), int(round(y))))

    def text(self, text: str, x: float, y: float) -> None:
        font = self.font
        color = self._norm_color(self.brush)
        surf = font.render(str(text), True, color)
        self._surface.blit(surf, (int(round(x)), int(round(y))))

    def measure_text(self, text: str) -> tuple:
        font = self.font
        if hasattr(font, "size"):
            return font.size(str(text))
        surf = font.render(str(text), True, (0, 0, 0))
        return surf.get_size()

    def window(self, x: float, y: float, width: float, height: float):
        return _Window(self, x, y, width, height)


class shapes:
    @staticmethod
    def rectangle(x: float, y: float, w: float, h: float, radius: float = 0) -> _Rectangle | _RoundedRectangle:
        # Device firmware supports optional radius parameter for rounded corners
        if radius > 0:
            return _RoundedRectangle(x, y, w, h, radius)
        return _Rectangle(x, y, w, h)

    @staticmethod
    def rounded_rectangle(x: float, y: float, w: float, h: float, radius: float, *corner_radii) -> _RoundedRectangle:
        return _RoundedRectangle(x, y, w, h, radius, *corner_radii)

    @staticmethod
    def circle(x: float, y: float, radius: float) -> _Circle:
        return _Circle(x, y, radius)

    @staticmethod
    def squircle(x: float, y: float, radius: float, n: float = 4.0) -> _Squircle:
        return _Squircle(x, y, radius, n)

    @staticmethod
    def line(x1: float, y1: float, x2: float, y2: float, thickness: float = 1.0) -> _Line:
        return _Line(x1, y1, x2, y2, thickness)

    @staticmethod
    def regular_polygon(x: float, y: float, radius: float, sides: int) -> _RegularPolygon:
        return _RegularPolygon(x, y, radius, sides)

    @staticmethod
    def arc(x: float, y: float, radius: float, start_deg: float, end_deg: float) -> _Arc:
        return _Arc(x, y, radius, start_deg, end_deg)

    @staticmethod
    def pie(x: float, y: float, radius: float, start_deg: float, end_deg: float) -> _Pie:
        return _Pie(x, y, radius, start_deg, end_deg)


class brushes:
    @staticmethod
    def color(r, g=None, b=None, a=255) -> tuple:
        def _clamp(value):
            return max(0, min(255, int(round(value))))

        if g is None and b is None:
            r = g = b = r
        return (_clamp(r), _clamp(g), _clamp(b), _clamp(a))

    @staticmethod
    def xor(r, g=None, b=None, a=255) -> tuple:
        # The real hardware performs a XOR blend; for the simulator,
        # approximating with a solid colour keeps visuals readable.
        return brushes.color(r, g, b, a)


def _parse_ppf(path: str):
    """Parse a `.ppf` bitmap pixel-font (the format used by the real badge's
    `PixelFont.load`). This is a reverse-engineered binary layout:

      magic        4 bytes  b"ppf!"
      reserved     4 bytes  (unused)
      num_glyphs   u16 BE
      unused       u16 BE   (not needed for rendering)
      name_len     u16 BE
      name         name_len bytes, then zero-padded up to a 48-byte header
      glyph table  num_glyphs * 6 bytes: [codepoint u16][width u16][unused u16]
      glyph bitmap num_glyphs * height * row_bytes bytes, glyphs in table
                   order; each row is a big-endian integer of `row_bytes`
                   bytes, MSB-first, the leftmost `width` bits are the pixels
      trailer      2 bytes (unused)

    `row_bytes` and `height` aren't stored directly - they're derived from
    the widest glyph's width and the remaining file size (both divide out
    exactly once you know the real row width, which is how this was
    reverse-engineered against the actual font files).
    """
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"ppf!":
        raise ValueError(f"Not a ppf font: {path}")

    num_glyphs = struct.unpack(">H", data[8:10])[0]
    default_advance = struct.unpack(">H", data[10:12])[0]
    name_len = struct.unpack(">H", data[12:14])[0]
    name = data[14:14 + name_len].split(b"\x00", 1)[0].decode("utf-8", "replace")

    table_start = 48
    entries = []
    max_width = 1
    for i in range(num_glyphs):
        off = table_start + i * 6
        code, width, _unused = struct.unpack(">HHH", data[off:off + 6])
        entries.append((code, width))
        if width > max_width:
            max_width = width

    table_end = table_start + num_glyphs * 6
    row_bytes = 2 * max(1, math.ceil(max_width / 16))
    blob_len = len(data) - table_end
    height = (blob_len + 2) // (row_bytes * num_glyphs)
    if height <= 0:
        raise ValueError(f"Could not determine glyph height for {path}")

    glyph_size = height * row_bytes
    glyphs = {}
    for idx, (code, width) in enumerate(entries):
        off = table_end + idx * glyph_size
        chunk = data[off:off + glyph_size]
        if len(chunk) < glyph_size:
            chunk = chunk + b"\x00" * (glyph_size - len(chunk))
        rows = [
            int.from_bytes(chunk[r * row_bytes:(r + 1) * row_bytes], "big")
            for r in range(height)
        ]
        glyphs[code] = (width, rows)

    return name, height, row_bytes, default_advance, glyphs


def _thin_row(value: int, width: int, bits: int, min_run: int = 3) -> int:
    """Shave the trailing pixel off runs of `min_run`-or-more consecutive
    set bits in a row. Used to shed a little weight off an especially bold
    font without ever fully erasing a thin (1-2px) stroke."""
    row = [(value >> (bits - 1 - c)) & 1 for c in range(width)]
    run_start = None
    for c in range(width + 1):
        on = row[c] if c < width else 0
        if on:
            if run_start is None:
                run_start = c
        elif run_start is not None:
            if c - run_start >= min_run:
                row[c - 1] = 0
            run_start = None
    result = 0
    for c, bit in enumerate(row):
        result |= bit << (bits - 1 - c)
    return result


class _PPFFont:
    """Renders text using a real badge `.ppf` pixel font, matching the
    hardware's glyph shapes and spacing instead of a generic system font."""

    _cache = {}

    def __init__(self, path: str):
        name, height, row_bytes, default_advance, glyphs = _parse_ppf(path)
        self.name = name
        self.height = height
        self._bits = row_bytes * 8
        if name.strip().lower().startswith("absolute"):
            # "Absolute" is the boldest of the bundled fonts by a wide
            # margin (its glyphs are ~47% ink vs. ~20-40% for the rest) and
            # reads a little too heavy on screen - trim a hair off its
            # thicker strokes. Thinner strokes (<3px) are left untouched.
            glyphs = {
                code: (width, [_thin_row(v, width, self._bits) for v in rows])
                for code, (width, rows) in glyphs.items()
            }
        self._glyphs = glyphs
        self._fallback_advance = default_advance or max(1, height // 2)
        self._mask_cache = {}
        self._tint_cache = {}

    @staticmethod
    def load(path: str) -> "_PPFFont":
        font = _PPFFont._cache.get(path)
        if font is None:
            font = _PPFFont(path)
            _PPFFont._cache[path] = font
        return font

    def get_height(self) -> int:
        return self.height

    def _advance(self, ch: str) -> int:
        glyph = self._glyphs.get(ord(ch))
        if glyph and glyph[0] > 0:
            return glyph[0]
        return self._fallback_advance

    def size(self, text) -> tuple:
        text = str(text)
        width = sum(self._advance(ch) for ch in text)
        return (width, self.height)

    def _glyph_mask(self, code: int):
        if code in self._mask_cache:
            return self._mask_cache[code]
        glyph = self._glyphs.get(code)
        mask = None
        if glyph and glyph[0] > 0:
            width, rows = glyph
            mask = pygame.Surface((width, self.height), pygame.SRCALPHA)
            mask.lock()
            for r, val in enumerate(rows):
                for c in range(width):
                    if (val >> (self._bits - 1 - c)) & 1:
                        mask.set_at((c, r), (255, 255, 255, 255))
            mask.unlock()
        self._mask_cache[code] = mask
        return mask

    def _tinted_glyph(self, code: int, color):
        key = (code, color)
        if key in self._tint_cache:
            return self._tint_cache[key]
        mask = self._glyph_mask(code)
        tinted = None
        if mask is not None:
            tinted = mask.copy()
            tinted.fill((color[0], color[1], color[2], color[3] if len(color) > 3 else 255),
                        special_flags=pygame.BLEND_RGBA_MULT)
        self._tint_cache[key] = tinted
        return tinted

    def render(self, text, antialias=True, color=(255, 255, 255, 255)) -> pygame.Surface:
        text = str(text)
        width, height = self.size(text)
        surf = pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)
        x = 0
        for ch in text:
            tinted = self._tinted_glyph(ord(ch), color)
            if tinted is not None:
                surf.blit(tinted, (x, 0))
            x += self._advance(ch)
        return surf


class PixelFont:
    class _Wrapper:
        __slots__ = ("_font", "name", "height")

        def __init__(self, font: pygame.font.Font, name: str):
            self._font = font
            self.name = name
            self.height = font.get_height()

        def render(self, *args, **kwargs):
            return self._font.render(*args, **kwargs)

        def size(self, *args, **kwargs):
            return self._font.size(*args, **kwargs)

        def get_height(self):
            return self._font.get_height()

        def __getattr__(self, item):
            return getattr(self._font, item)

    @staticmethod
    def load(path: str, size: int = 14):
        resolved = map_system_path(path)
        name = os.path.splitext(os.path.basename(path))[0]
        ext = os.path.splitext(resolved)[1].lower()

        if ext == ".ppf" and os.path.exists(resolved):
            try:
                font = _PPFFont.load(resolved)
                if _perf_monitor and _perf_monitor.enabled:
                    _perf_monitor.asset_tracker.register_font(resolved)
                return font
            except Exception:
                traceback.print_exc()

        font = None
        if os.path.exists(resolved) and ext in {".ttf", ".otf", ".ttc"}:
            try:
                font = pygame.font.Font(resolved, size)
            except Exception:
                font = None
        if font is None:
            font = pygame.font.Font(None, size)

        # Track font loading for performance monitoring
        if _perf_monitor and _perf_monitor.enabled:
            _perf_monitor.asset_tracker.register_font(resolved)

        return PixelFont._Wrapper(font, name)


class Image(_SurfaceTarget):
    OFF = 0
    X2 = 1
    X4 = 2
    _cache = {}

    def __init__(self, *args, _surface: pygame.Surface = None):
        if _surface is None:
            if len(args) == 2:
                width, height = args
            elif len(args) == 4:
                _, _, width, height = args
            else:
                raise TypeError("Image() expects width,height or x,y,width,height")
            width = max(1, int(round(width)))
            height = max(1, int(round(height)))
            surface = pygame.Surface((width, height), pygame.SRCALPHA)
        else:
            surface = _surface
            width = surface.get_width()
            height = surface.get_height()

        super().__init__(surface)
        self.width = width
        self.height = height
        self.antialias = Image.OFF
        self.has_palette = False
        self.x = 0
        self.y = 0
        if len(args) == 4:
            self.x, self.y = args[0], args[1]

    @property
    def alpha(self):
        return self._surface.get_alpha()

    @alpha.setter
    def alpha(self, value):
        self._surface.set_alpha(None if value is None else int(value))

    def get_width(self):
        return self.width

    def get_height(self):
        return self.height

    def __getattr__(self, item):
        return getattr(self._surface, item)

    @staticmethod
    def load(path: str):
        normalised = os.path.normpath(map_system_path(path))
        if normalised in Image._cache:
            source = Image._cache[normalised]
        else:
            source = pygame.image.load(normalised).convert_alpha()
            Image._cache[normalised] = source
            
            # Track asset loading for performance monitoring
            if _perf_monitor and _perf_monitor.enabled:
                width, height = source.get_size()
                _perf_monitor.asset_tracker.register_image(normalised, width, height)
        
        return Image(_surface=source.copy())


class SpriteSheet:
    def __init__(self, path: str, cols: int, rows: int) -> None:
        self.sheet = Image.load(path)
        self.cols = cols
        self.rows = rows
        self.frame_width = self.sheet.get_width() // cols
        self.frame_height = self.sheet.get_height() // rows

    def sprite(self, x: int, y: int) -> Image:
        rect = pygame.Rect(
            x * self.frame_width,
            y * self.frame_height,
            self.frame_width,
            self.frame_height,
        )
        image = pygame.Surface((self.frame_width, self.frame_height), pygame.SRCALPHA)
        src = self.sheet._surface if isinstance(self.sheet, Image) else self.sheet
        image.blit(src, (0, 0), rect)
        return Image(_surface=image)

    def animation(self, x: int = 0, y: int = 0, length: int = None):
        frames = []
        if length is None:
            length = self.cols * self.rows - (y * self.cols + x)
        for i in range(length):
            col = (x + i) % self.cols
            row = y + (x + i) // self.cols
            frames.append(self.sprite(col, row))
        return Animation(frames)


class Animation:
    def __init__(self, frames: list) -> None:
        self.frames = frames

    def frame(self, index: float) -> Image:
        i = int(index)
        if i < 0:
            i = 0
        elif i >= len(self.frames):
            i = len(self.frames) - 1
        return self.frames[i]

    def count(self) -> int:
        return len(self.frames)


# -----------------------------------------------------------------------------
# 2D Affine transform matrix (identity by default)
# Matches usage like: Matrix().translate(dx, dy)
# -----------------------------------------------------------------------------

class Matrix:
    """Simple 2D affine transform: | a c tx |
                                  | b d ty |
                                  | 0 0  1 |
    Supports chaining: Matrix().translate(x, y).scale(sx, sy).rotate(deg)
    """
    __slots__ = ("a", "b", "c", "d", "tx", "ty")

    def __init__(self, a: float = 1.0, b: float = 0.0, c: float = 0.0,
                 d: float = 1.0, tx: float = 0.0, ty: float = 0.0) -> None:
        self.a, self.b, self.c, self.d, self.tx, self.ty = a, b, c, d, tx, ty

    # fluent ops
    def translate(self, dx: float, dy: float):
        self.tx += self.a * dx + self.c * dy
        self.ty += self.b * dx + self.d * dy
        return self

    def scale(self, sx: float, sy: float = None):
        if sy is None:
            sy = sx
        self.a *= sx
        self.b *= sx
        self.c *= sy
        self.d *= sy
        return self

    def rotate(self, degrees: float):
        return self.rotate_radians(math.radians(degrees))

    def rotate_radians(self, radians: float):
        cos, sin = math.cos(radians), math.sin(radians)
        a, b, c, d = self.a, self.b, self.c, self.d
        self.a = a * cos + c * sin
        self.b = b * cos + d * sin
        self.c = -a * sin + c * cos
        self.d = -b * sin + d * cos
        return self

    def multiply(self, other: "Matrix"):
        a = self.a * other.a + self.c * other.b
        b = self.b * other.a + self.d * other.b
        c = self.a * other.c + self.c * other.d
        d = self.b * other.c + self.d * other.d
        tx = self.a * other.tx + self.c * other.ty + self.tx
        ty = self.b * other.tx + self.d * other.ty + self.ty
        self.a, self.b, self.c, self.d, self.tx, self.ty = a, b, c, d, tx, ty
        return self

    def transformed_point(self, x: float, y: float):
        return (self.a * x + self.c * y + self.tx, self.b * x + self.d * y + self.ty)


class Screen(_SurfaceTarget):
    # Physical size of the real badge's display, in millimetres.
    PHYSICAL_WIDTH_MM = 57.6
    PHYSICAL_HEIGHT_MM = 43.2
    # Empirical size correction for "real size" mode (dialed in by eye against
    # the physical badge). The +/- keys tweak further on top of this.
    REAL_SIZE_SCALE = 1.12

    # Where the display sits inside FRAME_IMAGE, in that image's own pixel
    # space (measured against images/render_003.png, 1864x2000).
    FRAME_SCREEN_RECT = (580, 1189, 718, 538)
    FRAME_DISPLAY_HEIGHT = 900  # window height, in px, when frame mode is on

    # Clickable hardware buttons in the mockup, in FRAME_IMAGE pixel space,
    # each mapped to the badge button it presses. (String literals match
    # IO.BUTTON_* - IO isn't defined yet at this point in the module.)
    FRAME_BUTTON_RECTS = {
        "BUTTON_A": (643, 1730, 130, 120),
        "BUTTON_B": (867, 1730, 130, 120),
        "BUTTON_C": (1092, 1730, 130, 120),
        "BUTTON_UP": (1292, 1287, 120, 120),
        "BUTTON_DOWN": (1292, 1537, 120, 120),
    }

    def __init__(self, width: int = 160, height: int = 120, scale: int = 4,
                 screenshot_dir: str = None, dpi: float = 96.0) -> None:
        self.width = width
        self.height = height
        self.scale = scale
        self.dpi = dpi
        self.screenshot_dir = screenshot_dir
        self._screenshot_counter = 0
        self.real_size_mode = False
        self.frame_mode = False
        self._frame_image = None
        self._frame_load_attempted = False
        # Persisted user calibration for real-size mode (see nudge_real_size);
        # multiplies the detected DPI so it can be dialled to physically exact
        # on any monitor, since reported display sizes aren't always accurate.
        self.real_size_cal = self._load_calibration()
        surface = pygame.Surface((width, height), pygame.SRCALPHA)
        super().__init__(surface)
        self.antialias = Image.OFF
        # A real anti-aliased UI font for the hint bar - the bundled pygame
        # default font renders grainy at small sizes.
        self._hint_font = pygame.font.SysFont(
            "Helvetica Neue,Helvetica,Arial,DejaVu Sans,Segoe UI,sans", 14)
        self._window = None
        self._apply_window_mode()
        pygame.display.set_caption("Badge Local Simulator")

    @staticmethod
    def _calibration_path() -> str:
        root = SIM_ROOT or _find_sim_root(os.getcwd())
        path = os.path.join(root, ".badge_state")
        os.makedirs(path, exist_ok=True)
        return os.path.join(path, "sim_calibration.json")

    def _load_calibration(self) -> float:
        try:
            with open(Screen._calibration_path(), "r", encoding="utf-8") as fh:
                val = float(json.load(fh).get("real_size_cal", 1.0))
                return min(4.0, max(0.25, val))
        except Exception:
            return 1.0

    def _save_calibration(self) -> None:
        try:
            with open(Screen._calibration_path(), "w", encoding="utf-8") as fh:
                json.dump({"real_size_cal": self.real_size_cal}, fh)
        except Exception:
            pass

    def _effective_dpi(self) -> float:
        return self.dpi * Screen.REAL_SIZE_SCALE * self.real_size_cal

    def _physical_size_px(self) -> tuple:
        """Window size, in on-screen pixels, that renders the 160x120
        framebuffer at the real badge's physical display size (57.6x43.2mm)
        given the monitor DPI (and the user's real-size calibration)."""
        mm_to_px = self._effective_dpi() / 25.4
        return (
            max(1, round(Screen.PHYSICAL_WIDTH_MM * mm_to_px)),
            max(1, round(Screen.PHYSICAL_HEIGHT_MM * mm_to_px)),
        )

    def nudge_real_size(self, factor: float) -> None:
        """Live-calibrate real-size mode (bound to +/-). Persists so the badge
        comes up physically exact next time."""
        self.real_size_cal = min(4.0, max(0.25, self.real_size_cal * factor))
        self._save_calibration()
        if self.real_size_mode:
            self._apply_window_mode()
        w, h = self._physical_size_px()
        print(f"[Simulator] Real-size calibration {self.real_size_cal:.2f}x -> {w}x{h}px")

    def _load_frame_image(self):
        if not self._frame_load_attempted:
            self._frame_load_attempted = True
            simulator_dir = os.path.dirname(os.path.abspath(__file__))
            frame_path = os.path.join(simulator_dir, "..", "images", "render_003.png")
            try:
                self._frame_image = pygame.image.load(frame_path).convert_alpha()
            except Exception as e:
                print(f"[Simulator] Could not load device frame ({frame_path}): {e}")
                self._frame_image = None
        return self._frame_image

    def _content_size(self) -> tuple:
        """Size, in window pixels, of the main display area (excludes the
        keyboard-hint bar)."""
        if self.frame_mode and self._load_frame_image() is not None:
            fw, fh = self._frame_image.get_size()
            scale = Screen.FRAME_DISPLAY_HEIGHT / fh
            return (round(fw * scale), round(fh * scale))
        if self.real_size_mode:
            return self._physical_size_px()
        return (self.width * self.scale, self.height * self.scale)

    def _show_hint_bar(self) -> bool:
        # Shown in the plain and device-frame views. Hidden only in real-size
        # mode, which is a true-to-life size reference a hint bar would swamp.
        return not self.real_size_mode

    def _apply_window_mode(self) -> None:
        cw, ch = self._content_size()
        if self._show_hint_bar():
            _, bar_h = self._toolbar(cw)
            ch += bar_h
        # Always a normal, titled window - keeps the macOS traffic-light
        # buttons (so it can be moved and closed) and shows the hint bar.
        self._window = pygame.display.set_mode((cw, ch))

    def toggle_real_size(self) -> None:
        self.real_size_mode = not self.real_size_mode
        self._apply_window_mode()
        if self.real_size_mode:
            w, h = self._physical_size_px()
            print(f"[Simulator] Real-size mode on: {w}x{h}px. "
                  f"Nudge with +/- if it's not the size you want.")
        else:
            print("[Simulator] Real-size mode off")

    def toggle_frame_mode(self) -> None:
        if not self.frame_mode and self._load_frame_image() is None:
            print("[Simulator] Device frame image is unavailable, staying in normal mode.")
            return
        self.frame_mode = not self.frame_mode
        self._apply_window_mode()
        print(f"[Simulator] Device frame mode {'on' if self.frame_mode else 'off'}")

    def set_icon(self, icon_path: str) -> None:
        """Set the application icon (displayed in dock/taskbar)."""
        try:
            icon_path = map_system_path(icon_path)
            if os.path.exists(icon_path):
                icon = pygame.image.load(icon_path)
                pygame.display.set_icon(icon)
            else:
                print(f"Icon file not found: {icon_path}")
        except Exception as e:
            print(f"Failed to set icon: {e}")

    def load_into(self, path: str) -> None:
        """Load an image directly into the screen buffer."""
        image = Image.load(path)
        src = self._unwrap(image)
        if src.get_width() != self.width or src.get_height() != self.height:
            src = pygame.transform.scale(src, (self.width, self.height))
        self._surface.blit(src, (0, 0))

    def window(self, x: float, y: float, width: float, height: float):
        return _Window(self, x, y, width, height)

    def take_screenshot(self) -> None:
        """Save a screenshot of the current screen at native resolution."""
        if self.screenshot_dir is None:
            print("Screenshot directory not configured. Use --screenshots to set it.")
            return
        
        # Ensure directory exists
        os.makedirs(self.screenshot_dir, exist_ok=True)
        
        # Generate filename
        filename = f"screenshot_{self._screenshot_counter:04d}.png"
        filepath = os.path.join(self.screenshot_dir, filename)
        self._screenshot_counter += 1
        
        # Save the native resolution surface (not the scaled version)
        pygame.image.save(self._surface, filepath)
        print(f"Screenshot saved: {filepath}")

    def _opaque_framebuffer(self) -> pygame.Surface:
        """Flatten the (per-pixel-alpha) drawing surface onto an opaque
        backing, matching the real hardware's framebuffer, which has no
        concept of transparency. Without this, any pixel the app hasn't
        explicitly drawn over lets whatever is *behind* the blit show
        through - fine on the plain display, but wrong when compositing
        into the device frame, where it let the mockup's own baked-in
        screenshot bleed through instead of being fully replaced."""
        opaque = pygame.Surface((self.width, self.height))
        opaque.fill((0, 0, 0))
        src = self._surface
        # Ignore any surface-wide alpha / colorkey an app may have left set,
        # so drawn pixels always transfer at full opacity (otherwise the
        # screen area can composite to near-black in the device frame).
        prev_alpha = src.get_alpha()
        prev_ck = src.get_colorkey()
        if prev_alpha is not None:
            src.set_alpha(None)
        if prev_ck is not None:
            src.set_colorkey(None)
        opaque.blit(src, (0, 0))
        if prev_alpha is not None:
            src.set_alpha(prev_alpha)
        if prev_ck is not None:
            src.set_colorkey(prev_ck)
        return opaque

    # (keycap text, what it does). ASCII only - many system fonts lack arrow
    # glyphs and render them as blank "tofu" boxes.
    _KEY_HINTS = [
        ("Z / Left", "A"),
        ("X / Enter", "B"),
        ("Space / Right", "C"),
        ("Arrows", "Move"),
        ("H / Esc", "Home"),
        ("R", "Reload"),
        ("Shift", "Real size"),
        ("+ / -", "Fit size"),
        ("F", "Frame"),
        ("P", "Charge"),
        ("F12", "Shot"),
    ]

    def _toolbar(self, width: int):
        """Build (and cache) the bottom hint bar for a given window width.
        Rendered as crisp anti-aliased key-cap 'chips' that flow onto extra
        rows if they don't fit, so every command is always shown. Returns
        (surface, height)."""
        if getattr(self, "_toolbar_cache", (None,))[0] == width:
            return self._toolbar_cache[1], self._toolbar_cache[2]

        pad_x, pad_y = 6, 3          # inside each key-cap chip
        gap = 8                      # chip -> its label
        item_gap = 20                # item -> next item
        margin = 12                  # bar left/right padding
        row_gap = 8
        bar_pad_y = 8

        font = self._hint_font
        chip_bg = (58, 60, 68)
        chip_fg = (236, 238, 242)
        label_fg = (150, 153, 162)
        bar_bg = (26, 27, 31)

        # Pre-render each item's key-cap chip and label.
        items = []
        row_h = 0
        for keys, label in Screen._KEY_HINTS:
            key_surf = font.render(keys, True, chip_fg)
            lbl_surf = font.render(label, True, label_fg)
            chip_w = key_surf.get_width() + pad_x * 2
            chip_h = key_surf.get_height() + pad_y * 2
            item_w = chip_w + gap + lbl_surf.get_width()
            item_h = max(chip_h, lbl_surf.get_height())
            row_h = max(row_h, item_h)
            items.append((key_surf, lbl_surf, chip_w, chip_h, item_w))

        # Flow items into rows that fit `width`.
        avail = max(1, width - margin * 2)
        rows, cur, cur_w = [], [], 0
        for it in items:
            add = it[4] + (item_gap if cur else 0)
            if cur and cur_w + add > avail:
                rows.append(cur)
                cur, cur_w = [], 0
                add = it[4]
            cur.append(it)
            cur_w += add
        if cur:
            rows.append(cur)

        bar_h = bar_pad_y * 2 + len(rows) * row_h + (len(rows) - 1) * row_gap
        bar = pygame.Surface((width, bar_h))
        bar.fill(bar_bg)

        y = bar_pad_y
        for row in rows:
            x = margin
            for key_surf, lbl_surf, chip_w, chip_h, item_w in row:
                chip_y = y + (row_h - chip_h) // 2
                chip_rect = pygame.Rect(x, chip_y, chip_w, chip_h)
                pygame.draw.rect(bar, chip_bg, chip_rect, border_radius=4)
                bar.blit(key_surf, (x + pad_x, chip_y + pad_y))
                lbl_x = x + chip_w + gap
                lbl_y = y + (row_h - lbl_surf.get_height()) // 2
                bar.blit(lbl_surf, (lbl_x, lbl_y))
                x += item_w + item_gap
            y += row_h + row_gap

        self._toolbar_cache = (width, bar, bar_h)
        return bar, bar_h

    def _frame_content_scale(self, cw: int):
        """Scale factor from FRAME_IMAGE pixel space to the on-screen frame
        content area (cw x frame-content-height)."""
        fw = self._frame_image.get_width()
        return cw / fw

    def button_at(self, pos):
        """Map a window (mouse) position to a badge button if it lands on one
        of the mockup's hardware buttons, else None. Only meaningful in frame
        mode - lets you 'press' the buttons in the image with the mouse."""
        if not self.frame_mode or self._load_frame_image() is None:
            return None
        cw, _ch = self._content_size()
        s = self._frame_content_scale(cw)
        fx = pos[0] / s
        fy = pos[1] / s
        for name, (bx, by, bw, bh) in Screen.FRAME_BUTTON_RECTS.items():
            if bx <= fx <= bx + bw and by <= fy <= by + bh:
                return name
        return None

    def _compose_frame(self, cw: int, ch: int, pressed=()) -> pygame.Surface:
        """Composite the live screen into the device-mockup image, and glow the
        hardware buttons that are currently pressed (via keyboard or mouse) so
        the button images visibly respond."""
        fx, fy, fw, fh = Screen.FRAME_SCREEN_RECT
        composed = self._frame_image.copy()  # RGBA
        # Nearest-neighbour keeps the badge's own pixels crisp; the opaque
        # framebuffer fully replaces the mockup's baked-in screenshot.
        live = pygame.transform.scale(self._opaque_framebuffer(), (fw, fh))
        composed.blit(live, (fx, fy))

        for name in pressed:
            rect = Screen.FRAME_BUTTON_RECTS.get(name)
            if not rect:
                continue
            bx, by, bw, bh = rect
            # Rounded-square glow to match the tactile buttons (not an oval).
            glow = pygame.Surface((bw, bh), pygame.SRCALPHA)
            radius = max(6, int(min(bw, bh) * 0.28))
            pygame.draw.rect(glow, (130, 240, 140, 110), glow.get_rect(),
                             border_radius=radius)
            pygame.draw.rect(glow, (180, 255, 190, 220), glow.get_rect(),
                             width=max(2, bw // 22), border_radius=radius)
            composed.blit(glow, (bx, by))

        return pygame.transform.smoothscale(composed, (cw, ch))

    def present(self) -> None:
        cw, ch = self._content_size()
        bar_h = 0
        if self._show_hint_bar():
            _, bar_h = self._toolbar(cw)

        if self.frame_mode and self._load_frame_image() is not None:
            pressed = io.held if ('io' in globals() and io is not None) else ()
            composed = self._compose_frame(cw, ch, pressed)
            # pygame can't present a truly transparent window (its software
            # framebuffer is opaque), so the see-through areas of the mockup
            # sit on a neutral backdrop rather than the desktop.
            self._window.fill((22, 23, 26))
            self._window.blit(composed, (0, 0))
        elif self.real_size_mode:
            self._window.fill((0, 0, 0))
            self._window.blit(
                pygame.transform.smoothscale(self._opaque_framebuffer(), (cw, ch)), (0, 0))
        else:
            # Plain windowed view: crisp nearest-neighbour upscale.
            self._window.blit(pygame.transform.scale(self._surface, (cw, ch)), (0, 0))

        if bar_h:
            bar, _ = self._toolbar(cw)
            self._window.blit(bar, (0, ch))

        pygame.display.flip()


class _Window:
    def __init__(self, parent: Screen, x: float, y: float, width: float, height: float):
        self._parent = parent
        self.x = int(round(x))
        self.y = int(round(y))
        self.width = max(0, int(round(width)))
        self.height = max(0, int(round(height)))
        self.brush = parent.brush
        self.font = parent.font

    def _set_clip(self):
        prev = self._parent._surface.get_clip()
        rect = pygame.Rect(self.x, self.y, self.width, self.height)
        self._parent._surface.set_clip(rect)
        return prev

    def _restore_clip(self, prev):
        self._parent._surface.set_clip(prev)

    def clear(self, color=None):
        clip = self._set_clip()
        try:
            fill_color = self._parent._norm_color(color if color is not None else self.brush)
            rect = pygame.Rect(self.x, self.y, self.width, self.height)
            self._parent._surface.fill(fill_color, rect)
        finally:
            self._restore_clip(clip)

    def draw(self, shape: _Shape) -> None:
        color = self._parent._norm_color(self.brush)
        clip = self._set_clip()
        try:
            _render_shape(self._parent._surface, color, shape, offset=(self.x, self.y))
        finally:
            self._restore_clip(clip)

    def _offset(self, x: float, y: float, transform: "Matrix" = None):
        if isinstance(transform, Matrix):
            x, y = transform.transformed_point(x, y)
        return x + self.x, y + self.y

    def blit(self, image, x: float, y: float, transform: "Matrix" = None) -> None:
        clip = self._set_clip()
        try:
            x, y = self._offset(x, y, transform)
            self._parent._surface.blit(
                self._parent._unwrap(image),
                (int(x), int(y)),
            )
        finally:
            self._restore_clip(clip)

    def scale_blit(self, image, x: float, y: float, w: int, h: int, transform: "Matrix" = None) -> None:
        clip = self._set_clip()
        try:
            x, y = self._offset(x, y, transform)
            src = self._parent._unwrap(image)
            new_w = max(1, abs(w))
            new_h = max(1, abs(h))
            scaled = pygame.transform.scale(src, (new_w, new_h))
            if w < 0:
                scaled = pygame.transform.flip(scaled, True, False)
            if h < 0:
                scaled = pygame.transform.flip(scaled, False, True)
            self._parent._surface.blit(scaled, (int(x), int(y)))
        finally:
            self._restore_clip(clip)

    def text(self, text: str, x: float, y: float) -> None:
        clip = self._set_clip()
        try:
            font = self.font or self._parent.font
            surf = font.render(str(text), True, self._parent._norm_color(self.brush))
            self._parent._surface.blit(surf, (int(x + self.x), int(y + self.y)))
        finally:
            self._restore_clip(clip)

    def measure_text(self, text: str) -> tuple:
        font = self.font or self._parent.font
        surf = font.render(str(text), True, (0, 0, 0))
        return surf.get_size()

    def window(self, x: float, y: float, width: float, height: float):
        return _Window(self._parent, self.x + x, self.y + y, width, height)


class IO:
    BUTTON_A = "BUTTON_A"
    BUTTON_B = "BUTTON_B"
    BUTTON_C = "BUTTON_C"
    BUTTON_UP = "BUTTON_UP"
    BUTTON_DOWN = "BUTTON_DOWN"
    BUTTON_LEFT = "BUTTON_LEFT"
    BUTTON_RIGHT = "BUTTON_RIGHT"
    BUTTON_HOME = "BUTTON_HOME"

    def __init__(self) -> None:
        self.pressed: set = set()
        self.down: set = set()
        self.released: set = set()
        self.changed: set = set()
        self.held: set = set()
        self.ticks = 0
        self.ticks_delta = 0
        self._last_ticks = pygame.time.get_ticks()
        # Each key can map to more than one logical button - e.g. LEFT/RIGHT
        # double up as A/C and Enter doubles up as B, so you can play without
        # taking a hand off the arrow keys.
        self._key_map = {
            pygame.K_a: (IO.BUTTON_A,),
            pygame.K_b: (IO.BUTTON_B,),
            pygame.K_c: (IO.BUTTON_C,),
            pygame.K_UP: (IO.BUTTON_UP,),
            pygame.K_DOWN: (IO.BUTTON_DOWN,),
            pygame.K_LEFT: (IO.BUTTON_LEFT, IO.BUTTON_A),
            pygame.K_RIGHT: (IO.BUTTON_RIGHT, IO.BUTTON_C),
            pygame.K_z: (IO.BUTTON_A,),
            pygame.K_x: (IO.BUTTON_B,),
            pygame.K_SPACE: (IO.BUTTON_C,),
            pygame.K_RETURN: (IO.BUTTON_B,),
            pygame.K_KP_ENTER: (IO.BUTTON_B,),
            pygame.K_h: (IO.BUTTON_HOME,),
            pygame.K_ESCAPE: (IO.BUTTON_HOME,),
        }
        # Simulator-only keys - not badge buttons, so games never see them
        # via `pressed`/`down`/etc. `run()` checks `meta_pressed` directly.
        self._meta_key_map = {
            pygame.K_r: "RELOAD",
            pygame.K_LSHIFT: "TOGGLE_REAL_SIZE",
            pygame.K_RSHIFT: "TOGGLE_REAL_SIZE",
            pygame.K_f: "TOGGLE_FRAME",
            pygame.K_p: "TOGGLE_CHARGING",
            pygame.K_EQUALS: "CAL_UP",     # '=' / '+'
            pygame.K_PLUS: "CAL_UP",
            pygame.K_KP_PLUS: "CAL_UP",
            pygame.K_MINUS: "CAL_DOWN",
            pygame.K_KP_MINUS: "CAL_DOWN",
        }
        self.meta_pressed: set = set()
        # Badge button currently held via a mouse click on the device frame.
        self._mouse_btn = None

    def update(self) -> None:
        self.pressed.clear()
        self.released.clear()
        self.meta_pressed.clear()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                pygame.quit()
                sys.exit(0)
            if event.type == pygame.KEYDOWN:
                # Handle screenshot key (F12)
                if event.key == pygame.K_F12:
                    screen.take_screenshot()
                elif event.key in self._key_map:
                    for name in self._key_map[event.key]:
                        self.pressed.add(name)
                        self.down.add(name)
                if event.key in self._meta_key_map:
                    self.meta_pressed.add(self._meta_key_map[event.key])
            if event.type == pygame.KEYUP:
                if event.key in self._key_map:
                    for name in self._key_map[event.key]:
                        self.down.discard(name)
                        self.released.add(name)
            # Clicking the hardware buttons drawn in the device frame presses
            # the matching badge button.
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                name = screen.button_at(event.pos) if screen is not None else None
                if name:
                    self.pressed.add(name)
                    self.down.add(name)
                    self._mouse_btn = name
            if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                if self._mouse_btn:
                    self.down.discard(self._mouse_btn)
                    self.released.add(self._mouse_btn)
                    self._mouse_btn = None
        self.held = set(self.down)
        self.changed = set()
        self.changed.update(self.pressed)
        self.changed.update(self.released)
        now = pygame.time.get_ticks()
        self.ticks_delta = now - self._last_ticks
        self.ticks = now
        self._last_ticks = now


class Display:
    def update(self) -> None:
        # Present the current screen contents.
        screen.present()

display = Display()


class State:
    @staticmethod
    def _state_dir() -> str:
        root = SIM_ROOT or _find_sim_root(os.getcwd())
        path = os.path.join(root, ".badge_state")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def _state_path(name: str) -> str:
        safe = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_"))
        if not safe:
            safe = "state"
        return os.path.join(State._state_dir(), f"{safe}.json")

    @staticmethod
    def load(name: str, target) -> bool:
        path = State._state_path(name)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(target, dict) and isinstance(data, dict):
                target.update(data)
            return True
        except FileNotFoundError:
            return False
        except Exception:
            traceback.print_exc()
            return False

    @staticmethod
    def save(name: str, data) -> bool:
        path = State._state_path(name)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            return True
        except Exception:
            traceback.print_exc()
            return False


def clamp(value: float, minimum: float, maximum: float) -> float:
    if value < minimum:
        return minimum
    if value > maximum:
        return maximum
    return value

# -----------------------------------------------------------------------------
# File helpers expected by some games (menu, etc.)
# -----------------------------------------------------------------------------

def is_dir(path: str) -> bool:
    return os.path.isdir(map_system_path(path))

def file_exists(path: str) -> bool:
    return os.path.isfile(map_system_path(path))


# Simulated charging state. A badge sitting in its dock/plugged into USB is
# charging, so we default to True. Press `P` in the simulator to toggle it and
# exercise charge-aware apps (e.g. the marquee's low-power mode).
_charging = True


def get_battery_level() -> int:
    """Return a fake but plausible battery percentage."""
    return 75


def is_charging() -> bool:
    """Return whether the badge is currently charging."""
    return _charging

# -----------------------------------------------------------------------------
# Mock network module for WiFi simulation
# -----------------------------------------------------------------------------

# Global reference to io object for timing (set during load_game_module)
_io_ref = None

class _MockWLAN:
    """Mock WLAN interface that simulates WiFi connectivity using host network."""
    
    def __init__(self, interface_id):
        self._interface_id = interface_id
        self._active = False
        self._connected = False
        self._ssid = None
        self._password = None
        self._connect_time = None
        self._secrets_ssid = None
        
        # Try to read SSID from secrets.py
        try:
            secrets_path = map_system_path("/")
            if secrets_path:
                secrets_file = os.path.join(secrets_path, "secrets.py")
                if os.path.exists(secrets_file):
                    with open(secrets_file, 'r') as f:
                        for line in f:
                            if line.strip().startswith('WIFI_SSID'):
                                # Extract SSID from line like: WIFI_SSID = "u25-badger-party"
                                parts = line.split('=', 1)
                                if len(parts) == 2:
                                    ssid = parts[1].strip().strip('"').strip("'")
                                    if ssid:
                                        self._secrets_ssid = ssid
                                        break
        except Exception:
            pass
        
    def active(self, state=None):
        """Get or set the active state of the WLAN interface."""
        if state is None:
            return self._active
        self._active = bool(state)
        return self._active
    
    def isconnected(self):
        """Check if connected to a network."""
        # Simulate connection delay (takes ~1-2 seconds)
        if self._connect_time is not None:
            # Use io.ticks from global reference for consistent timing
            if _io_ref is not None:
                elapsed = _io_ref.ticks - self._connect_time
            else:
                # Fallback to pygame ticks if io not available yet
                elapsed = pygame.time.get_ticks() - self._connect_time
            if elapsed > 1500:  # 1.5 second connection time
                self._connected = True
        return self._connected
    
    def scan(self):
        """Simulate WiFi scan - return fake networks including the one being connected to."""
        # Return a list of tuples: (ssid, bssid, channel, RSSI, security, hidden)
        networks = [
            (b"GH Events", b"\x00\x11\x22\x33\x44\x66", 11, -70, 3, False),
            (b"FreeWiFi", b"\x00\x11\x22\x33\x44\x77", 1, -75, 0, False),
        ]
        
        # Add SSID from secrets.py if we found one
        if self._secrets_ssid:
            ssid_bytes = self._secrets_ssid.encode('utf-8') if isinstance(self._secrets_ssid, str) else self._secrets_ssid
            if not any(net[0] == ssid_bytes for net in networks):
                networks.insert(0, (ssid_bytes, b"\x00\x11\x22\x33\x44\x55", 6, -50, 3, False))
        
        # Add the requested SSID if one was specified and different from secrets
        if self._ssid and self._ssid != self._secrets_ssid:
            ssid_bytes = self._ssid.encode('utf-8') if isinstance(self._ssid, str) else self._ssid
            # Check if already in list
            if not any(net[0] == ssid_bytes for net in networks):
                networks.insert(0, (ssid_bytes, b"\xAA\xBB\xCC\xDD\xEE\xFF", 1, -45, 3, False))
        
        return networks
    
    def connect(self, ssid, password=None):
        """Simulate connecting to a WiFi network (accepts any password)."""
        # Only initiate connection if not already connecting to this network
        if self._ssid == ssid and self._connect_time is not None:
            # Already connecting to this SSID, don't reset the timer
            return
            
        self._ssid = ssid
        self._password = password
        # Use io.ticks from global reference for consistent timing
        if _io_ref is not None:
            self._connect_time = _io_ref.ticks
        else:
            # Fallback to pygame ticks if io not available yet
            self._connect_time = pygame.time.get_ticks()
        self._connected = False  # Will become True after delay
        print(f"[Simulator] Connecting to WiFi: {ssid}")
    
    def disconnect(self):
        """Disconnect from the network."""
        self._connected = False
        self._connect_time = None
        self._ssid = None
        self._password = None
        print("[Simulator] Disconnected from WiFi")
    
    def ifconfig(self):
        """Return network interface configuration."""
        if self._connected:
            return ("192.168.1.100", "255.255.255.0", "192.168.1.1", "8.8.8.8")
        return ("0.0.0.0", "0.0.0.0", "0.0.0.0", "0.0.0.0")


class _MockNetwork:
    """Mock network module matching MicroPython's network API."""
    STA_IF = 0  # Station interface (client mode)
    AP_IF = 1   # Access Point interface
    
    @staticmethod
    def WLAN(interface_id):
        """Create a WLAN network interface object."""
        return _MockWLAN(interface_id)


# -----------------------------------------------------------------------------
# Mock urllib.urequest for MicroPython compatibility
# -----------------------------------------------------------------------------

# Store reference to real urllib.request before we create mocks
import urllib.request as _real_urllib_request

class _MockUrequestResponse:
    """Mock response object for urlopen that uses Python's urllib."""
    
    def __init__(self, real_response):
        self._response = real_response
        self.status_code = real_response.status
    
    def read(self, size=-1):
        """Read response data."""
        return self._response.read(size)
    
    def readinto(self, buffer):
        """Read response data into a buffer (MicroPython style)."""
        data = self._response.read(len(buffer))
        if not data:
            return 0
        buffer[:len(data)] = data
        return len(data)
    
    def close(self):
        """Close the response."""
        self._response.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


class _MockUrequest:
    """Mock urllib.urequest module for MicroPython compatibility."""
    
    @staticmethod
    def urlopen(url, data=None, headers=None):
        """Open a URL and return a response object."""
        # Use the real urllib.request we saved earlier
        if headers:
            req = _real_urllib_request.Request(url, data=data, headers=headers)
        else:
            req = _real_urllib_request.Request(url, data=data)
        
        try:
            response = _real_urllib_request.urlopen(req)
            return _MockUrequestResponse(response)
        except Exception as e:
            print(f"[Simulator] HTTP Error: {e}")
            raise

# -----------------------------------------------------------------------------
# Runner
# -----------------------------------------------------------------------------

def _cleanup_pycache():
    """Remove __pycache__ directories in the game/app directory."""
    try:
        import shutil
        sim_root = SIM_ROOT or _find_sim_root(os.getcwd())
        apps_dir = os.path.join(sim_root, "apps")
        if os.path.isdir(apps_dir):
            for root, dirs, files in os.walk(apps_dir):
                if "__pycache__" in dirs:
                    pycache_path = os.path.join(root, "__pycache__")
                    try:
                        shutil.rmtree(pycache_path)
                    except Exception:
                        pass
    except Exception:
        pass

def run(update_func, fps: int = 60, init=None, on_exit=None):
    if not callable(init):
        module_name = getattr(update_func, "__module__", None)
        module_obj = sys.modules.get(module_name) if module_name else None
        if module_obj is not None:
            init = getattr(module_obj, "init", None)
            if not callable(on_exit):
                on_exit = getattr(module_obj, "on_exit", None)
    clock = pygame.time.Clock()
    result = None
    
    # Get performance monitor from global if available
    perf_monitor = globals().get('_perf_monitor', None)
    
    try:
        if callable(init):
            init()
        while True:
            io.update()
            
            # Check for Home button press to return to menu
            if IO.BUTTON_HOME in io.pressed:
                result = "__RETURN_TO_MENU__"
                break

            # Simulator-only meta keys (not badge buttons)
            if "RELOAD" in io.meta_pressed:
                result = "__RELOAD__"
                break
            if "TOGGLE_REAL_SIZE" in io.meta_pressed:
                screen.toggle_real_size()
            if "TOGGLE_FRAME" in io.meta_pressed:
                screen.toggle_frame_mode()
            if "CAL_UP" in io.meta_pressed:
                screen.nudge_real_size(1.03)
            if "CAL_DOWN" in io.meta_pressed:
                screen.nudge_real_size(1 / 1.03)
            if "TOGGLE_CHARGING" in io.meta_pressed:
                global _charging
                _charging = not _charging
                print(f"[Simulator] Charging {'connected' if _charging else 'disconnected'}")

            result = update_func()
            screen.present()
            clock.tick(fps)
            
            # Update performance metrics if enabled
            if perf_monitor:
                perf_monitor.update(clock)
            
            if result is not None:
                break
    finally:
        if callable(on_exit):
            try:
                on_exit()
            except Exception:
                traceback.print_exc()
        # Clean up __pycache__ directory
        _cleanup_pycache()
    return result

# -----------------------------------------------------------------------------
# Module loader
# -----------------------------------------------------------------------------

def load_game_module(module_path: str) -> ModuleType:
    """Load a game module from a path or dotted module. Inject our `badgeware`."""
    if module_path.endswith(".py"):
        game_abs = os.path.abspath(map_system_path(module_path))
        spec = importlib.util.spec_from_file_location("badge_game", game_abs)
    else:
        spec = importlib.util.find_spec(module_path)
        if spec is None:
            raise ImportError(f"Cannot find module {module_path}")
        origin = getattr(spec, "origin", None)
        game_abs = os.path.abspath(origin) if origin else os.getcwd()
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load specification for {module_path}")

    # Make local imports work (e.g. `from mona import Mona`)
    game_dir = os.path.dirname(game_abs)
    sim_root = SIM_ROOT if SIM_ROOT is not None else _find_sim_root(game_dir)
    simulator_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Add paths only if not already present to avoid accumulation
    for p in (game_dir, os.path.join(sim_root, "apps"), simulator_dir):
        if p not in sys.path:
            sys.path.insert(0, p)

    # Provide `badgeware`
    badgeware = ModuleType("badgeware")
    badgeware.screen = screen
    badgeware.Image = Image
    badgeware.SpriteSheet = SpriteSheet
    badgeware.PixelFont = PixelFont
    badgeware.brushes = brushes
    badgeware.shapes = shapes
    badgeware.io = io
    badgeware.run = run
    badgeware.Matrix = Matrix
    badgeware.is_dir = is_dir
    badgeware.file_exists = file_exists
    badgeware.get_battery_level = get_battery_level
    badgeware.is_charging = is_charging
    badgeware.display = display
    badgeware.State = State
    badgeware.clamp = clamp
    sys.modules["badgeware"] = badgeware
    
    # Set global reference for mock network timing
    global _io_ref
    _io_ref = io
    
    # Provide mock `network` module for WiFi apps
    network_module = ModuleType("network")
    network_module.WLAN = _MockNetwork.WLAN
    network_module.STA_IF = _MockNetwork.STA_IF
    network_module.AP_IF = _MockNetwork.AP_IF
    sys.modules["network"] = network_module
    
    # Provide mock `urllib` with `urequest` submodule for MicroPython compatibility
    # Create the main urllib module
    urllib_module = ModuleType("urllib")
    
    # Create urllib.urequest as a submodule
    urequest_module = ModuleType("urllib.urequest")
    urequest_module.urlopen = _MockUrequest.urlopen
    
    # Add urequest to urllib
    urllib_module.urequest = urequest_module
    
    # Register both modules
    sys.modules["urllib"] = urllib_module
    sys.modules["urllib.urequest"] = urequest_module
    
    # Also provide a top-level urequest for direct imports
    sys.modules["urequest"] = urequest_module
    
    # Provide mock `urandom` module for MicroPython compatibility
    # Uses Python's standard random module
    urandom_module = ModuleType("urandom")
    import random as _random
    
    def _urandom_getrandbits(n):
        """Get n random bits as an integer."""
        return _random.getrandbits(n)
    
    def _urandom_randint(a, b):
        """Return random integer in range [a, b], including both end points."""
        return _random.randint(a, b)
    
    def _urandom_randrange(*args):
        """randrange([start,] stop[, step]) - like range() but returns random value."""
        return _random.randrange(*args)
    
    def _urandom_choice(seq):
        """Choose a random element from a non-empty sequence."""
        return _random.choice(seq)
    
    def _urandom_random():
        """Return random float in [0.0, 1.0)."""
        return _random.random()
    
    def _urandom_uniform(a, b):
        """Return random float in [a, b] or [a, b) depending on rounding."""
        return _random.uniform(a, b)
    
    urandom_module.getrandbits = _urandom_getrandbits
    urandom_module.randint = _urandom_randint
    urandom_module.randrange = _urandom_randrange
    urandom_module.choice = _urandom_choice
    urandom_module.random = _urandom_random
    urandom_module.uniform = _urandom_uniform
    sys.modules["urandom"] = urandom_module
    
    # Provide mock `aye_arr` module for IR receiver/transmitter functionality
    # This is hardware-specific and won't work in the simulator, but we can mock it
    # to allow apps to load without errors
    
    # Base RemoteDescriptor class
    class _MockRemoteDescriptor:
        """Mock base class for IR remote descriptors."""
        NAME = "Mock Remote"
        ADDRESS = 0x00
        BUTTON_CODES = {}
        
        def __init__(self):
            self._on_known = None
            self._on_unknown = None
        
        @property
        def on_known(self):
            return self._on_known
        
        @on_known.setter
        def on_known(self, callback):
            self._on_known = callback
        
        @property
        def on_unknown(self):
            return self._on_unknown
        
        @on_unknown.setter
        def on_unknown(self, callback):
            self._on_unknown = callback
    
    # Mock NECReceiver class
    class _MockNECReceiver:
        """Mock IR receiver that simulates receiving codes."""
        
        def __init__(self, pin, pio=0, sm=0):
            self.pin = pin
            self.pio = pio
            self.sm = sm
            self._descriptor = None
            self._running = False
            self._simulate_code = None
            self._last_simulate_time = 0
        
        def bind(self, descriptor):
            """Bind a remote descriptor to this receiver."""
            self._descriptor = descriptor
        
        def start(self):
            """Start the receiver."""
            self._running = True
            print(f"[Simulator] IR Receiver started (mocked) - press 1-9 to simulate beacon codes")
        
        def stop(self):
            """Stop the receiver."""
            self._running = False
        
        def decode(self):
            """Decode received IR signals (simulated via keyboard)."""
            if not self._running or not self._descriptor:
                return
            
            # Simulate IR codes with number keys (1-9)
            # Check for number key presses to simulate beacon detection
            import pygame
            keys = pygame.key.get_pressed()
            
            # Rate limit simulation to once per second
            current_time = pygame.time.get_ticks()
            if current_time - self._last_simulate_time < 1000:
                return
            
            # Check for number keys 1-9
            for key_num in range(1, 10):
                key_code = getattr(pygame, f'K_{key_num}', None)
                if key_code and keys[key_code]:
                    self._last_simulate_time = current_time
                    button_code = self._descriptor.BUTTON_CODES.get(key_num)
                    if button_code and self._descriptor.on_known:
                        print(f"[Simulator] IR beacon {key_num} detected (simulated)")
                        self._descriptor.on_known(key_num)
                    break
    
    # Create aye_arr module structure
    aye_arr_module = ModuleType("aye_arr")
    
    # Create aye_arr.nec submodule
    aye_arr_nec_module = ModuleType("aye_arr.nec")
    aye_arr_nec_module.NECReceiver = _MockNECReceiver
    
    # Create aye_arr.nec.remotes submodule
    aye_arr_nec_remotes_module = ModuleType("aye_arr.nec.remotes")
    
    # Create aye_arr.nec.remotes.descriptor submodule
    aye_arr_nec_remotes_descriptor_module = ModuleType("aye_arr.nec.remotes.descriptor")
    aye_arr_nec_remotes_descriptor_module.RemoteDescriptor = _MockRemoteDescriptor
    
    # Link everything together
    aye_arr_nec_remotes_module.descriptor = aye_arr_nec_remotes_descriptor_module
    aye_arr_nec_module.remotes = aye_arr_nec_remotes_module
    aye_arr_module.nec = aye_arr_nec_module
    
    # Register all modules
    sys.modules["aye_arr"] = aye_arr_module
    sys.modules["aye_arr.nec"] = aye_arr_nec_module
    sys.modules["aye_arr.nec.remotes"] = aye_arr_nec_remotes_module
    sys.modules["aye_arr.nec.remotes.descriptor"] = aye_arr_nec_remotes_descriptor_module

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore
    return mod

# -----------------------------------------------------------------------------
# Performance monitoring
# -----------------------------------------------------------------------------

class AssetTracker:
    """Track loaded assets to estimate MicroPython memory usage on the badge."""
    
    def __init__(self):
        self.images = {}  # path -> (width, height, bytes)
        self.fonts = set()
        self.peak_images = 0
        
    def register_image(self, path, width, height):
        """Register an image and estimate its memory footprint."""
        if path not in self.images:
            # MicroPython images: 2 bytes per pixel (RGB565) is typical
            # Full RGBA would be 4 bytes/pixel, paletted can be 1-2 bytes
            # Use 2 bytes as a reasonable average
            estimated_bytes = width * height * 2
            self.images[path] = (width, height, estimated_bytes)
            if len(self.images) > self.peak_images:
                self.peak_images = len(self.images)
    
    def unregister_image(self, path):
        """Remove an image from tracking (when unloaded)."""
        if path in self.images:
            del self.images[path]
    
    def register_font(self, path):
        """Track a loaded font."""
        self.fonts.add(path)
    
    def get_total_kb(self):
        """Get total estimated memory for all tracked assets."""
        total_bytes = sum(img[2] for img in self.images.values())
        # Fonts are typically 10-50KB each, use 20KB average
        total_bytes += len(self.fonts) * 20 * 1024
        return total_bytes / 1024
    
    def get_largest_image_kb(self):
        """Get size of the largest loaded image."""
        if not self.images:
            return 0
        return max(img[2] for img in self.images.values()) / 1024
    
    def reset(self):
        """Clear all tracked assets."""
        self.images.clear()
        self.fonts.clear()


class PerformanceMonitor:
    """Track and display CPU, memory usage, and badge asset estimates."""
    
    def __init__(self, enabled=False):
        self.enabled = enabled
        if enabled:
            import psutil
            self.psutil = psutil
            self.process = psutil.Process(os.getpid())
            self.last_time = None
            self.frame_count = 0
            self.fps_sum = 0
            self.update_interval = 0.5  # Update metrics every 0.5 seconds
            self.last_update = 0
            self.baseline_memory = None  # Track baseline after first app loads
            self.initial_memory = None   # Track memory at first measurement
            self.peak_memory = 0         # Track peak memory growth
            self.asset_tracker = AssetTracker()  # Track loaded assets
    
    def set_baseline(self):
        """Set the baseline memory after app loads and first frame renders."""
        if self.enabled and self.baseline_memory is None:
            mem_info = self.process.memory_info()
            self.baseline_memory = mem_info.rss / 1024 / 1024  # MB
            self.initial_memory = self.baseline_memory
            self.peak_memory = 0
    
    def update(self, clock):
        """Update and display performance metrics."""
        if not self.enabled:
            return
        
        import time
        current_time = time.time()
        
        # Only update display at specified interval
        if current_time - self.last_update < self.update_interval:
            return
        
        self.last_update = current_time
        
        # Set baseline on first update (after app has loaded)
        if self.baseline_memory is None:
            self.set_baseline()
            return
        
        # Get FPS from pygame clock
        fps = clock.get_fps()
        
        # Calculate frame time in milliseconds (more meaningful than CPU%)
        # Badge target is 60 FPS = 16.67ms per frame
        # If frame time > 16.67ms, badge will drop frames
        frame_time_ms = (1000.0 / fps) if fps > 0 else 0
        
        # Estimate badge CPU usage based on frame time
        # Badge has ~16.67ms budget at 60 FPS
        # If we're taking longer, we're "over budget"
        badge_frame_budget_ms = 16.67
        frame_budget_percent = (frame_time_ms / badge_frame_budget_ms) * 100
        
        # Get CPU usage (percentage for this process)
        cpu_percent = self.process.cpu_percent(interval=0.1)
        
        # Get memory usage
        mem_info = self.process.memory_info()
        mem_mb = mem_info.rss / 1024 / 1024  # Convert to MB
        
        # Calculate memory growth since baseline (what the app is using/leaking)
        memory_growth_mb = mem_mb - self.baseline_memory
        memory_growth_kb = memory_growth_mb * 1024
        
        # Track peak growth
        if memory_growth_kb > self.peak_memory:
            self.peak_memory = memory_growth_kb
        
        # Get estimated badge memory from asset tracking
        estimated_badge_kb = self.asset_tracker.get_total_kb()
        largest_image_kb = self.asset_tracker.get_largest_image_kb()
        image_count = len(self.asset_tracker.images)
        font_count = len(self.asset_tracker.fonts)
        
        # Badge has 512KB SRAM total, but realistically apps have ~300-400KB available
        # (system uses some for badgeware, drivers, etc.)
        badge_available_kb = 400
        
        # Status based on estimated badge memory
        if estimated_badge_kb > badge_available_kb:
            warning = " ⚠️  OVER LIMIT!"
        elif estimated_badge_kb > badge_available_kb * 0.75:
            warning = " ⚡ High"
        elif estimated_badge_kb > badge_available_kb * 0.50:
            warning = " ⚡ Med"
        else:
            warning = " ✓"
        
        # CPU status based on frame budget (more meaningful than CPU%)
        # Badge needs to complete each frame in 16.67ms to maintain 60 FPS
        if frame_time_ms > badge_frame_budget_ms * 1.5:
            cpu_status = " ⚠️  Slow!"
        elif frame_time_ms > badge_frame_budget_ms:
            cpu_status = " ⚡"
        else:
            cpu_status = " ✓"
        
        # Display with both Python memory and badge estimates
        print(f"\r[Perf] FPS:{fps:5.1f} Frame:{frame_time_ms:5.1f}ms{cpu_status} | "
              f"Badge~{estimated_badge_kb:5.1f}KB{warning} | "
              f"Imgs:{image_count}({largest_image_kb:5.1f}KB) Fonts:{font_count}", 
              end='', flush=True)

def _unload_app_modules(game_dir: str) -> None:
    """Tear down everything the previous app's modules touched so the next
    load - the menu, another app, or a hot reload of the same app - starts
    clean, mirroring the badge freeing memory when it switches apps."""
    if game_dir:
        game_dir_abs = os.path.abspath(game_dir)
        for p in [p for p in sys.path if os.path.abspath(p).startswith(game_dir_abs)]:
            while p in sys.path:
                sys.path.remove(p)

        # Purge this app's compiled bytecode cache directly rather than
        # relying on _cleanup_pycache()'s later sweep: __pycache__ is keyed
        # by source mtime, so a reload within the same mtime tick as an edit
        # could otherwise silently serve stale bytecode.
        import shutil
        for root, dirs, _files in os.walk(game_dir_abs):
            if "__pycache__" in dirs:
                try:
                    shutil.rmtree(os.path.join(root, "__pycache__"))
                except Exception:
                    pass

    modules_to_remove = []
    for mod_name, mod in sys.modules.items():
        if mod and hasattr(mod, "__file__") and mod.__file__:
            mod_file = os.path.abspath(mod.__file__)
            if game_dir and mod_file.startswith(game_dir):
                modules_to_remove.append(mod_name)
    for mod_name in modules_to_remove:
        del sys.modules[mod_name]

    # Also remove the main module loaded as "badge_game", plus common app
    # modules that can conflict (like ui, icon) - they'll be re-imported
    # fresh on the next load.
    for mod_name in ("badge_game", "ui", "icon", "beacon", "mona"):
        if mod_name in sys.modules:
            del sys.modules[mod_name]

    # Clear image cache to simulate badge behavior (old app's images are freed)
    Image._cache.clear()

    if _perf_monitor and _perf_monitor.enabled:
        _perf_monitor.asset_tracker.reset()

    import gc
    collected = gc.collect()
    if collected > 0:
        print(f"[Simulator] Garbage collected {collected} objects")


# -----------------------------------------------------------------------------
# DPI detection (for "real size" mode)
# -----------------------------------------------------------------------------

def _detect_dpi() -> float:
    """Best-effort autodetection of the monitor's real pixel density in
    *device pixels* (the units pygame's window surface uses here), so 'real
    size' mode is physically accurate on whatever machine you're running on
    without having to look up your own DPI by hand. Falls back to 96 if
    detection isn't possible."""
    system = platform.system()
    try:
        if system == "Darwin":
            return _detect_dpi_macos()
        if system == "Windows":
            return _detect_dpi_windows()
        if system == "Linux":
            return _detect_dpi_linux()
    except Exception:
        pass
    return 96.0


def _detect_dpi_macos() -> float:
    import ctypes
    import ctypes.util

    lib_path = ctypes.util.find_library("CoreGraphics")
    if not lib_path:
        raise RuntimeError("CoreGraphics not found")
    cg = ctypes.CDLL(lib_path)

    class CGSize(ctypes.Structure):
        _fields_ = [("width", ctypes.c_double), ("height", ctypes.c_double)]

    cg.CGMainDisplayID.restype = ctypes.c_uint32
    cg.CGDisplayScreenSize.restype = CGSize
    cg.CGDisplayScreenSize.argtypes = [ctypes.c_uint32]
    cg.CGDisplayPixelsWide.restype = ctypes.c_size_t
    cg.CGDisplayPixelsWide.argtypes = [ctypes.c_uint32]

    display_id = cg.CGMainDisplayID()
    size_mm = cg.CGDisplayScreenSize(display_id)
    # Use the *logical* ("points") width, because SDL/pygame sizes its window
    # in points on macOS - measured on screen, a set_mode((400,x)) window is
    # exactly 400 points wide. So points-per-inch is the density that makes a
    # window physically 57.6mm. (An earlier attempt used native pixels, which
    # came out 2x too big on Retina.) If a display's reported physical size is
    # inaccurate this can still be off - the +/- live calibration corrects it.
    points_wide = cg.CGDisplayPixelsWide(display_id)
    if size_mm.width <= 0 or points_wide <= 0:
        raise RuntimeError("CoreGraphics returned no usable display size")
    return points_wide / (size_mm.width / 25.4)


def _detect_dpi_windows() -> float:
    import ctypes

    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    hdc = user32.GetDC(0)
    try:
        LOGPIXELSX = 88
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, LOGPIXELSX)
    finally:
        user32.ReleaseDC(0, hdc)
    if not dpi:
        raise RuntimeError("GetDeviceCaps returned no DPI")
    return float(dpi)


def _detect_dpi_linux() -> float:
    import re
    import subprocess

    # Most X11 desktops set Xft.dpi to the user's actual configured DPI.
    try:
        out = subprocess.run(["xrdb", "-query"], capture_output=True, text=True, timeout=2)
        m = re.search(r"Xft\.dpi:\s*(\d+)", out.stdout)
        if m:
            return float(m.group(1))
    except Exception:
        pass

    # Fall back to the physical size xrandr reports for the first
    # connected output.
    out = subprocess.run(["xrandr", "--query"], capture_output=True, text=True, timeout=2)
    m = re.search(r"connected.*?(\d+)x(\d+)\+\d+\+\d+.*?(\d+)mm x (\d+)mm", out.stdout)
    if not m:
        raise RuntimeError("xrandr output didn't match")
    px_w, _px_h, mm_w, _mm_h = (int(g) for g in m.groups())
    if mm_w <= 0:
        raise RuntimeError("xrandr reported no physical size")
    return px_w / (mm_w / 25.4)


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run a GitHub Badge game locally using Pygame.")
    parser.add_argument("game", help="Path to the game .py, directory containing __init__.py, or a dotted module name.")
    parser.add_argument("--scale", type=int, default=4, help="Scale factor (default: 4)")
    parser.add_argument(
        "-C",
        "--system-root",
        dest="system_root",
        metavar="DIR",
        help="Use DIR as the root for '/system' lookups and asset loading.",
    )
    parser.add_argument(
        "--screenshots",
        dest="screenshot_dir",
        metavar="DIR",
        help="Directory to save screenshots (press F12 to capture).",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean temporary files (cached downloads, state) before starting.",
    )
    parser.add_argument(
        "--perf",
        action="store_true",
        help="Show live performance metrics (CPU and memory usage) in terminal.",
    )
    parser.add_argument(
        "--dpi",
        type=float,
        default=None,
        help="Override your monitor's pixels-per-inch (auto-detected by default) used "
             "to size the Shift 'real size' preview to the badge's actual 57.6x43.2mm "
             "display. Pass this if the auto-detected value looks wrong.",
    )
    args = parser.parse_args()
    
    # Clean temporary files if requested
    if args.clean:
        import tempfile
        import shutil
        root_dir = os.path.join(tempfile.gettempdir(), "badge_simulator_root")
        if os.path.exists(root_dir):
            try:
                shutil.rmtree(root_dir)
                print(f"Cleaned temporary files: {root_dir}")
            except Exception as e:
                print(f"Warning: Could not clean temporary files: {e}")
    
    # Initialize performance monitoring
    global _perf_monitor
    if args.perf:
        try:
            import psutil  # type: ignore
            _perf_monitor = PerformanceMonitor(enabled=True)
            print("[Simulator] Performance monitoring enabled")
        except ImportError:
            print("[Simulator] Warning: psutil not installed. Install with 'pip install psutil' to enable --perf")
            print("[Simulator] Continuing without performance monitoring...")
            _perf_monitor = None
    else:
        _perf_monitor = None

    pygame.init()

    global screen, io, SIM_ROOT
    if args.dpi is not None:
        dpi = args.dpi
        print(f"[Simulator] Using --dpi {dpi:.1f}")
    else:
        dpi = _detect_dpi()
        print(f"[Simulator] Detected display DPI: {dpi:.1f} "
              f"(pass --dpi to override if Shift 'real size' mode looks wrong)")
    screen = Screen(scale=args.scale, screenshot_dir=args.screenshot_dir, dpi=dpi)
    io = IO()
    
    # Set system root with default to ./badge relative to simulator
    if args.system_root:
        root = os.path.abspath(args.system_root)
        if not os.path.isdir(root):
            print(f"System root '{args.system_root}' is not a directory.", file=sys.stderr)
            pygame.quit()
            sys.exit(2)
        SIM_ROOT = root
    else:
        # Default to ./badge relative to the simulator directory
        simulator_dir = os.path.dirname(os.path.abspath(__file__))
        default_root = os.path.join(simulator_dir, "..", "badge")
        if os.path.isdir(default_root):
            SIM_ROOT = os.path.abspath(default_root)
        else:
            SIM_ROOT = _find_sim_root(os.getcwd())
    
    # Performance monitor will set baseline automatically after first app loads
    if _perf_monitor:
        print("[Simulator] Memory profiler enabled - tracking memory growth (baseline set after app loads)")
    
    # Main app loop - allows apps to launch other apps
    current_app = args.game
    
    while True:
        # If current_app is a directory, append __init__.py
        game_path = current_app
        game_dir = None
        app_name = "Badge App"
        if os.path.isdir(game_path):
            game_dir = game_path
            app_name = os.path.basename(os.path.abspath(game_path))
            init_path = os.path.join(game_path, "__init__.py")
            if os.path.isfile(init_path):
                game_path = init_path
            else:
                print(f"Directory '{game_path}' does not contain __init__.py", file=sys.stderr)
                pygame.quit()
                sys.exit(1)
        else:
            # If it's a file, use its directory
            game_dir = os.path.dirname(os.path.abspath(game_path))
            app_name = os.path.basename(game_dir)
        
        # Set window title with app name
        pygame.display.set_caption(f"Badge Simulator - {app_name}")
        
        # Try to set app icon from the game's directory
        if game_dir:
            icon_path = os.path.join(game_dir, "icon.png")
            if os.path.isfile(icon_path):
                screen.set_icon(icon_path)

        try:
            module = load_game_module(game_path)
        except SystemExit:
            raise
        except Exception as e:
            print(f"[Simulator Error] Failed to load game module: {e}", file=sys.stderr)
            traceback.print_exc()
            pygame.quit()
            sys.exit(1)

        if not hasattr(module, "update"):
            print("Loaded module has no 'update' function", file=sys.stderr)
            pygame.quit()
            sys.exit(1)

        try:
            init_func = getattr(module, "init", None)
            exit_func = getattr(module, "on_exit", None)
            result = run(module.update, init=init_func, on_exit=exit_func)
            
            # Hot-reload: re-import the same app from disk without tearing
            # down pygame/SDL, so edits show up without restarting the sim.
            if result == "__RELOAD__":
                print(f"\n[Simulator] Reloading {app_name}...")
                _unload_app_modules(game_dir)
                # current_app is left unchanged, so the next iteration
                # re-reads game_path fresh from disk.
                continue

            # Check if user pressed Home button to return to menu
            if result == "__RETURN_TO_MENU__":
                menu_path = os.path.join(SIM_ROOT, "apps", "menu")
                if os.path.isdir(menu_path) and os.path.isfile(os.path.join(menu_path, "__init__.py")):
                    print(f"\n[Simulator] Returning to menu")
                    current_app = menu_path
                    _unload_app_modules(game_dir)
                    # Continue to next iteration to load the menu
                    continue
                else:
                    print(f"\n[Simulator] Menu app not found, exiting")
                    break

            # If the app returned a path to another app, load it
            elif result and isinstance(result, str):
                # Check if it's a valid app path
                result_path = map_system_path(result)
                if os.path.isdir(result_path) and os.path.isfile(os.path.join(result_path, "__init__.py")):
                    print(f"\n[Simulator] Launching app: {result}")
                    current_app = result_path
                    _unload_app_modules(game_dir)
                    # Continue to next iteration to load the new app
                    continue
                else:
                    print(f"\n[Simulator] Invalid app path returned: {result}")
                    break
            else:
                # App exited normally without launching another app
                break
                
        except SystemExit:
            # Allow clean exit (e.g., user requested quit); suppress traceback and exit quietly.
            break
        except Exception:
            traceback.print_exc()
            pygame.quit()
            sys.exit(1)
    
    # Clean up and exit
    if _perf_monitor and _perf_monitor.enabled:
        print()  # Newline after performance metrics
    pygame.quit()



if __name__ == "__main__":
    main()
