from pathlib import Path
from PIL import Image
import math

root = Path(__file__).resolve().parents[1] / 'app' / 'static' / 'mascots'
for name in ('white', 'blue', 'black', 'red', 'green'):
    source = Image.open(root / f'{name}.png').convert('RGBA')
    source.thumbnail((260, 260), Image.Resampling.LANCZOS)
    frames = []
    for frame in range(24):
        phase = frame * math.tau / 24
        sprite = source.rotate(1.1 * math.sin(phase), resample=Image.Resampling.BICUBIC, expand=True)
        canvas = Image.new('RGBA', (288, 288), (0, 0, 0, 0))
        canvas.alpha_composite(sprite, ((288 - sprite.width) // 2, (288 - sprite.height) // 2 + round(2 * math.sin(phase))))
        alpha = canvas.getchannel('A')
        paletted = canvas.convert('RGB').quantize(colors=255, method=Image.Quantize.MEDIANCUT)
        paletted.paste(255, mask=alpha.point(lambda a: 255 if a < 96 else 0))
        paletted.info['transparency'] = 255
        frames.append(paletted)
    frames[0].save(root / f'{name}.gif', save_all=True, append_images=frames[1:], duration=125, loop=0, disposal=2, transparency=255, optimize=False)
