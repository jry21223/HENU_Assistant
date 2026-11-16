"""实际发布包必须独立携带图片渲染资源，不能借用源码目录。"""
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

import pytest


def test_lbp_package_renders_images_without_source_assets(tmp_path):
    root = Path(__file__).resolve().parents[1]
    lbp = Path(sys.executable).with_name("lbp")
    if not lbp.is_file():
        command = shutil.which("lbp")
        if command is None:
            pytest.skip("需要开发打包命令 lbp")
        lbp = Path(command)
    output = tmp_path / "package"
    subprocess.run([str(lbp), "build", "-o", str(output)], cwd=root,
                   check=True, capture_output=True, text=True, timeout=60)
    package, = output.glob("*.lbpkg")
    extracted = tmp_path / "unpacked"
    with ZipFile(package) as archive:
        required = {"henu_plugin/assets/" + name for name in (
            "HENUUI-coverage.data", "HENUUI-OFL.txt", "classroom-plans.data",
            "HENUUI-Regular.ttf", "HENUUI-Medium.ttf", "HENUUI-Bold.ttf",
        )}
        assert required <= set(archive.namelist())
        archive.extractall(extracted)
    subprocess.run([sys.executable, "-I", "-c", """
import io
from pathlib import Path
import sys
from PIL import Image, ImageDraw

root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
from henu_plugin import classroom_floorplans, classroom_map, font_assets, seat_map_image
for module in (classroom_floorplans, classroom_map, font_assets, seat_map_image):
    assert Path(module.__file__).resolve().is_relative_to(root)
seat = seat_map_image.render_seat_map('座位图', '2026-11-15 08:00–23:00', [
    dict(no='1', point_x=10, point_y=10, width=5, height=5,
         status='1', status_name='空闲')], '12:00')
assert Image.open(io.BytesIO(seat)).format == 'PNG'
entry = classroom_floorplans.plans()['03-23']
names = [room['name'] for room in entry['rooms']]
scope = dict(termCode='2026,1', week=10, dayOfWeek=5, period=2,
             campusCode='03', buildingCode='23')
result = dict(success=True, _resolved_tool_name='empty_classroom_query',
    query={**scope, 'campusName': entry['campus'], 'buildingName': entry['name']},
    rooms=[dict(roomName=name, campusCode='03', buildingCode='23') for name in names],
    total=len(names), freshness=dict(liveChecked=True, roomStates=dict(
        complete=True, scope=scope, free=names, occupied=[])))
png = classroom_map.render_classroom_map(result)
assert Image.open(io.BytesIO(png)).format == 'PNG'
scope.update(campusCode='02', buildingCode='21')
result['query'] = {**scope, 'campusName': '金明校区', 'buildingName': '金明综合楼6'}
result['rooms'] = [dict(roomName='金明综合楼6101', campusCode='02', buildingCode='21')]
result['total'] = 1
result['freshness']['roomStates'] = dict(complete=True, scope=scope,
    free=['金明综合楼6101'], occupied=[])
directory = Image.open(io.BytesIO(classroom_map.render_classroom_map(result)))
assert directory.format == 'PNG' and directory.info['View'] == 'floor-directory'
try:
    font_assets.draw_text(ImageDraw.Draw(Image.new('RGB', (80, 40))),
                          (0, 0), '\\uE000', 16, font_assets.new_font_cache())
except ValueError as exc:
    assert 'U+E000' in str(exc)
else:
    raise AssertionError('包内绘字不得绕过缺字保护')
""", str(extracted)], cwd=tmp_path, check=True,
                   capture_output=True, text=True, timeout=60)
