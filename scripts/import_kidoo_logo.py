#!/usr/bin/env python3
"""Generate Kidoo wordmarks and launcher art from the supplied SVG.

Requires ImageMagick with SVG support for the bitmap fallbacks and web logo.
The first path in this particular artwork is its near-white background.
"""
from pathlib import Path
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'assets/branding/kidoo_logo.svg'
RES = ROOT / 'android-tv/app/src/main/res'
ANDROID = 'http://schemas.android.com/apk/res/android'
SVG = 'http://www.w3.org/2000/svg'
ET.register_namespace('android', ANDROID)
ET.register_namespace('', SVG)
def attr(name): return '{' + ANDROID + '}' + name

source = ET.parse(SOURCE).getroot()
if source.get('viewBox') != '0 0 9050 3017':
    raise ValueError('Unexpected source artwork: verify crop and background before importing')
group = next(x for x in source if x.tag == '{%s}g' % SVG)
background = group[0]
if background.get('style') != 'fill:#fcfcfd;':
    raise ValueError('Unexpected background; refusing to remove artwork')
group.remove(background)
paths = []
for path in group:
    match = re.fullmatch(r'fill:(#[0-9a-fA-F]{6});', path.get('style', ''))
    if path.tag != '{%s}path' % SVG or not match:
        raise ValueError('Unsupported SVG artwork')
    paths.append({attr('fillColor'):match.group(1), attr('fillType'):'evenOdd',
                  attr('pathData'):path.attrib['d']})

# The crop contains the entire mark with a little clear space around it.
crop_x, crop_y, crop_w, crop_h = 1200, 140, 6500, 2700
out = RES / 'drawable-v24'
out.mkdir(parents=True, exist_ok=True)
for name, width, height, vw, vh, tx, ty, background_color in (
    ('kidoo_wordmark_vector','200dp','83.08dp',6500,2700,-1200,-140,None),
    ('kidoo_launcher_art','200dp','200dp',7500,7500,-700,2260,'#FFFFFFFF'),
    ('kidoo_banner_art','320dp','180dp',7500,4218.75,-700,619.375,'#FF091630'),
):
    vector = ET.Element('vector',{attr('width'):width,attr('height'):height,
                                 attr('viewportWidth'):str(vw),attr('viewportHeight'):str(vh)})
    if background_color:
        ET.SubElement(vector,'path',{attr('fillColor'):background_color,
                                     attr('pathData'):f'M0,0 H{vw} V{vh} H0 Z'})
    layer = ET.SubElement(vector,'group',{attr('translateX'):str(tx),attr('translateY'):str(ty)})
    for path in paths: ET.SubElement(layer,'path',path)
    ET.ElementTree(vector).write(out / (name+'.xml'),encoding='utf-8',xml_declaration=True)

source.set('viewBox',f'{crop_x} {crop_y} {crop_w} {crop_h}')
source.set('width','2600'); source.set('height','1080')
with tempfile.TemporaryDirectory() as temp:
    svg = Path(temp)/'wordmark.svg'
    ET.ElementTree(source).write(svg,encoding='utf-8',xml_declaration=True)
    mark = RES/'drawable-nodpi/kidoo_wordmark.png'
    subprocess.run(['magick','-background','none',str(svg),'-resize','2000x','-depth','8',str(mark)],check=True)
    subprocess.run(['magick',str(mark),'-resize','880x880','-background','white','-gravity','center','-extent','1024x1024',str(RES/'drawable-nodpi/kidoo_app_icon.png')],check=True)
    subprocess.run(['magick',str(mark),'-resize','1100x500','-background','#091630','-gravity','center','-extent','1280x720',str(RES/'drawable-nodpi/kidoo_tv_banner.png')],check=True)
    subprocess.run(['magick',str(mark),'-resize','1400x','-depth','8',str(ROOT/'server/home/kidoo-logo-refined.png')],check=True)
print(f'Imported {len(paths)} vector paths and generated TV and web artwork.')
