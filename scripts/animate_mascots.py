"""Assemble eight articulated key poses per mascot, without rotating stills."""
from collections import deque
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('white', 'blue', 'black', 'red', 'green')


def clear_backdrop(image):
    """Remove edge-connected near-white matte for binary GIF transparency."""
    image = image.convert('RGBA')
    pixels = image.load()
    width, height = image.size
    seen = set()
    queue = deque([(x, y) for x in range(width) for y in (0, height-1)] +
                  [(x, y) for y in range(height) for x in (0, width-1)])
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen or not (0 <= x < width and 0 <= y < height):
            continue
        seen.add((x, y))
        r, g, b, a = pixels[x, y]
        if a < 235 or (min(r, g, b) > 218 and max(r, g, b)-min(r, g, b) < 24):
            pixels[x, y] = (0, 0, 0, 0)
            queue.extend(((x-1, y), (x+1, y), (x, y-1), (x, y+1)))
        elif a:
            pixels[x, y] = (r, g, b, 255)
    return image


def foot_anchor(image):
    """Align the orange feet, independently of asymmetric accessories."""
    points = [(x, y) for y in range(int(image.height*.72), image.height)
              for x in range(image.width)
              if (p := image.getpixel((x, y)))[3] > 128
              and p[0] > 160 and 60 < p[1] < 200 and p[2] < 90]
    if not points:
        raise ValueError('Cannot find orange feet in sprite cell')
    return sum(x for x, _ in points)/len(points), max(y for _, y in points)+1


def build_frames(sheet):
    poses = []
    for index in range(8):
        left, top = round(index % 4 * sheet.width/4), round(index//4 * sheet.height/2)
        right, bottom = round((index % 4+1)*sheet.width/4), round((index//4+1)*sheet.height/2)
        pose = clear_backdrop(sheet.crop((left, top, right, bottom)))
        anchor = foot_anchor(pose)
        bbox = pose.getchannel('A').getbbox()
        if bbox is None:
            raise ValueError('Empty sprite cell')
        poses.append((pose, anchor, bbox))
    # A common scale prevents props appearing from shrinking the body.
    reach = max(max(ax-box[0], box[2]-ax) for _, (ax, _), box in poses)
    above = max(ay-box[1] for _, (_, ay), box in poses)
    below = max(box[3]-ay for _, (_, ay), box in poses)
    scale = min(128/reach, 248/above, 12/max(1, below))
    frames = []
    for pose, (ax, ay), _ in poses:
        sprite = pose.resize((round(pose.width*scale), round(pose.height*scale)), Image.Resampling.LANCZOS)
        canvas = Image.new('RGBA', (288, 288))
        canvas.alpha_composite(sprite, (round(144-ax*scale), 268-round(ay*scale)))
        frames.append(canvas)
    return frames


def save_animation(frames, path):
    # Shared palette prevents quantization colors flickering between poses.
    atlas = Image.new('RGB', (288*len(frames), 288), '#0c191e')
    for index, frame in enumerate(frames):
        atlas.paste(frame, (index*288, 0), frame)
    palette = atlas.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    encoded = []
    for frame in frames:
        paletted = frame.convert('RGB').quantize(palette=palette, dither=Image.Dither.NONE)
        paletted.paste(255, mask=frame.getchannel('A').point(lambda a: 255 if a < 128 else 0))
        encoded.append(paletted)
    encoded[0].save(path, save_all=True, append_images=encoded[1:],
                    duration=[650, 180, 220, 220, 200, 260, 220, 550],
                    loop=0, disposal=2, transparency=255, optimize=False)


def main():
    output = ROOT/'app/static/mascots'
    preview = ROOT/'artifacts/mascots-animation'
    for name in NAMES:
        preview.mkdir(parents=True, exist_ok=True)
        with Image.open(ROOT/'docs/mascot-animation/source'/f'{name}.png') as sheet:
            frames = build_frames(sheet)
        save_animation(frames, output/f'{name}.gif')
        frames[0].save(output/f'{name}-still.png')
        contact = Image.new('RGBA', (288*4, 288*2))
        for index, frame in enumerate(frames):
            contact.alpha_composite(frame, ((index % 4)*288, (index//4)*288))
        contact.save(preview/f'{name}-frames.png')
        print(f'{name}: {len(frames)} articulated poses')


if __name__ == '__main__':
    main()
