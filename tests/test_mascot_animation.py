import importlib.util
from pathlib import Path

from PIL import Image, ImageDraw


def load_builder():
    spec = importlib.util.spec_from_file_location('mascot_builder', Path(__file__).parents[1] / 'scripts/animate_mascots.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sheet_frames_align_feet_without_scaling_each_pose():
    builder = load_builder()
    sheet = Image.new('RGBA', (400, 200))
    draw = ImageDraw.Draw(sheet)
    for index in range(8):
        x, y = (index % 4) * 100, (index // 4) * 100
        shift = index % 3 * 3
        draw.ellipse((x+30+shift, y+20, x+60+shift, y+75), fill='black')
        draw.rectangle((x+30+shift, y+75, x+60+shift, y+85), fill=(240, 140, 20, 255))
        if index == 3:
            draw.rectangle((x+60+shift, y+35, x+85, y+40), fill='black')
    frames = builder.build_frames(sheet)
    assert len(frames) == 8
    for frame in frames:
        assert frame.size == (288, 288)
        feet = [(x, y) for y in range(288) for x in range(288)
                if (p := frame.getpixel((x, y)))[3] > 128 and p[0] > 160 and 60 < p[1] < 200 and p[2] < 90]
        assert abs(sum(x for x, _ in feet) / len(feet) - 144) < 2
        assert max(y for _, y in feet) == 267
        assert frame.getpixel((0, 0))[3] == 0
