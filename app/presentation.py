"""Lokale titlar og rangeringsgrafikk. AI-tekst går berre til tekstfiler."""

from pathlib import Path
import os
import shutil
import textwrap

from .media import MediaError


COLORS = ("0xFFD166", "0xF56EB3", "0x63D9ED", "0xA8E282", "0xB59AFF")


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise MediaError("Tittelen eller rangeringsteksten er ugyldig.")
    return value.strip()


def _font(workdir):
    destination = workdir / "overlay-font.ttf"
    if destination.is_file():
        return
    candidates = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeuib.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            shutil.copyfile(path, destination)
            return
    raise MediaError("Fann ingen lokal skrifttype for videotitlar. Installer Segoe UI, DejaVu Sans eller Arial.")


def filters(plan, index, width, height, workdir):
    """Filter med kontrollerte filnamn; FFmpeg må køyre med workdir som cwd."""
    settings = plan[0].get("presentation") or {}
    title = settings.get("title")
    ranked = plan[index].get("ranking")
    if not title and not ranked:
        return []
    workdir = Path(workdir)
    _font(workdir)
    output = []
    counter = 0

    def draw(text, size, x, y, color="white", enable=None):
        nonlocal counter
        filename = f"overlay-{index:02d}-{counter:02d}.txt"
        counter += 1
        (workdir / filename).write_text(text, encoding="utf-8")
        # expansion=none hindrar at t.d. %{...} frå modellen blir tolka.
        result = (f"drawtext=fontfile=overlay-font.ttf:textfile={filename}:expansion=none:"
                  f"fontsize={int(size)}:fontcolor={color}:x={x}:y={y}:shadowcolor=black@0.5:shadowx=1:shadowy=2")
        if enable:
            result += f":enable='{enable}'"
        output.append(result)

    if index == 0 and title:
        title = _text(title, 60)
        seconds = min(1.4, float(plan[0]["duration"]) * 0.45)
        enabled = f"lt(t,{seconds:.4f})"
        output.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=0x101426@0.65:t=fill:enable='{enabled}'")
        size = max(24, int(min(width * 0.06, height * 0.075)))
        wrapped = textwrap.fill(title, width=max(10, int(width * 0.85 / (size * 0.65))))
        draw(wrapped, size, "(w-text_w)/2", "(h-text_h)/2", enable=enabled)

    if ranked:
        margin = max(16, int(min(width, height) * 0.035))
        panel_width = int(width * (0.72 if width < height else 0.44))
        heading_size = max(18, int(min(width, height) * 0.037))
        row_height = int(heading_size * 1.75)
        panel_height = margin * 2 + heading_size + row_height * (index + 1)
        output.append(f"drawbox=x={margin}:y={margin}:w={panel_width}:h={panel_height}:color=0x101426@0.82:t=fill")
        header = _text(title or "Rangering", 60)
        header_size = min(heading_size, max(10, int((panel_width - 2 * margin) / (len(header) * 1.05))))
        draw(header, header_size, 2 * margin, 2 * margin, "0xD7CEFF")
        for position, segment in enumerate(plan[:index + 1]):
            ranking = segment.get("ranking") or {}
            rank = ranking.get("rank")
            if type(rank) is not int or not 1 <= rank <= 10:
                raise MediaError("Rangeringa manglar ei gyldig plassering.")
            label = _text(ranking.get("title"), 36)
            y = 2 * margin + heading_size + row_height * position + 10
            color = COLORS[(rank - 1) % len(COLORS)]
            if position == index:
                output.append(f"drawbox=x={margin + 8}:y={y - 4}:w={panel_width - 16}:h={row_height}:color={color}@0.16:t=fill")
            draw(f"{rank}.", heading_size + 3, 2 * margin, y, color)
            label_x = 2 * margin + int(heading_size * 2.2)
            available = panel_width - label_x
            label_size = min(heading_size - 2, max(10, int(available / (len(label) * 1.05))))
            draw(label, label_size, label_x, y + 3)
    return output
