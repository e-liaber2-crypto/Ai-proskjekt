"""Lokale titlar, klipptekstar og rangeringsgrafikk."""

from pathlib import Path
import os
import shutil
import textwrap

from .media import MAX_CLIP_LABEL_CHARS, MAX_VIDEO_TEXT_CHARS, MediaError


COLORS = ("0xFFD166", "0xF56EB3", "0x63D9ED", "0xA8E282", "0xB59AFF")


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or not value.isprintable():
        raise MediaError("Teksten i videoen er ugyldig.")
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


def filters(plan, index, width, height, workdir, *, video_text=None):
    """Filter med kontrollerte filnamn; FFmpeg må køyre med workdir som cwd."""
    settings = plan[0].get("presentation") or {}
    title = settings.get("title")
    ranked = plan[index].get("ranking")
    caption = plan[index].get("caption")
    duration = float(plan[index]["duration"])
    title_end = min(1.4, duration * 0.45) if index == 0 and title else 0
    caption_start = max(0.12, title_end)
    caption_end = min(duration - 0.05, caption_start + 3)
    show_caption = caption and caption_end - caption_start >= 0.5

    text_enable = None
    if video_text:
        # Tidspunkta gjeld heile eksporten. FFmpeg brukar lokal tid i kvar del.
        total = sum(part["duration"] for part in plan)
        offset = sum(part["duration"] for part in plan[:index])
        timing = video_text["timing"]
        start = max(0, total - 3) if timing == "ending" else 0
        end = min(3, total) if timing == "opening" else total
        local_start = max(0, start - offset)
        local_end = min(duration, end - offset)
        if local_end - local_start > 1e-6:
            text_enable = f"gte(t,{local_start:.6f})*lt(t,{local_end:.6f})"

    if not title_end and not ranked and not show_caption and not text_enable:
        return []
    workdir = Path(workdir)
    _font(workdir)
    output = []
    counter = 0

    def draw(text, size, x, y, color="white", enable=None, padding=0):
        nonlocal counter
        filename = f"overlay-{index:02d}-{counter:02d}.txt"
        counter += 1
        (workdir / filename).write_text(text, encoding="utf-8")
        # expansion=none hindrar at t.d. %{...} i teksten blir tolka.
        result = (f"drawtext=fontfile=overlay-font.ttf:textfile={filename}:expansion=none:"
                  f"fontsize={int(size)}:fontcolor={color}:x={x}:y={y}:shadowcolor=black@0.5:shadowx=1:shadowy=2")
        if padding:
            result += f":box=1:boxcolor=0x101426@0.82:boxborderw={padding}"
        if enable:
            result += f":enable='{enable}'"
        output.append(result)

    if title_end:
        title = _text(title, 60)
        enabled = f"lt(t,{title_end:.4f})"
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
    bottom = int(height * 0.12)
    if text_enable:
        size = max(18, int(min(width, height) * 0.04))
        lines = textwrap.wrap(_text(video_text["text"], MAX_VIDEO_TEXT_CHARS),
                              width=max(12, int(width * 0.8 / size)))
        padding = max(8, int(size * 0.36))
        draw("\n".join(lines), size, "(w-text_w)/2", f"h-text_h-{bottom}",
             enable=text_enable, padding=padding)
        bottom += len(lines) * (size + 4) + 2 * padding
    if show_caption:
        size = max(18, int(min(width, height) * 0.044))
        caption = textwrap.fill(_text(caption, MAX_CLIP_LABEL_CHARS),
                                width=max(12, int(width * 0.82 / (size * 0.85))))
        draw(caption, size, "(w-text_w)/2", f"h-text_h-{bottom}",
             enable=f"between(t,{caption_start:.4f},{caption_end:.4f})",
             padding=max(8, int(size * 0.36)))
    return output
