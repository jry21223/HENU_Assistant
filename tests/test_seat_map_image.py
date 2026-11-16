import io


def test_hd_map_preserves_school_coordinates_and_reserved_state():
    from PIL import Image
    from henu_plugin.seat_map_image import render_seat_map, seat_state

    rows = [
        {
            "no": "001",
            "point_x": "0.260417",
            "point_y": "81.25",
            "width": "1.875",
            "height": "3.365385",
            "status": "2",
            "status_name": "已预约",
        },
        {
            "no": "002",
            "point_x": "0.208333",
            "point_y": "77.5",
            "width": "1.875",
            "height": "3.653846",
            "status": "1",
            "status_name": "空闲",
        },
        {
            "no": "003",
            "point_x": "2.34375",
            "point_y": "81.25",
            "width": "1.875",
            "height": "3.269231",
            "status": "99",
            "status_name": "未定义",
        },
    ]
    assert [seat_state(row) for row in rows] == ["reserved", "free", "unknown"]
    assert seat_state({"status": "1", "status_name": "已预约"}) == "unknown"
    data = render_seat_map(
        "金明校区 · 701 七层南自习室", "2026-10-21 08:00–23:00", rows, "14:10"
    )
    image = Image.open(io.BytesIO(data))
    assert image.size == (4096, 2560)
    # An independently computed point inside each actual school seat rectangle.
    assert image.getpixel((100, 2110))[:3] == (220, 74, 74)
    assert image.getpixel((100, 2028))[:3] == (35, 155, 112)


def test_school_coordinate_jitter_does_not_make_rows_and_seat_sizes_crooked():
    from PIL import Image, ImageChops
    from henu_plugin.seat_map_image import render_seat_map, COLORS

    rows = [
        {
            "no": "001",
            "point_x": 0.26,
            "point_y": 81.25,
            "width": 1.875,
            "height": 3.365,
            "status": "2",
            "status_name": "已预约",
        },
        {
            "no": "002",
            "point_x": 0.20,
            "point_y": 77.50,
            "width": 1.875,
            "height": 3.65,
            "status": "1",
            "status_name": "空闲",
        },
        {
            "no": "003",
            "point_x": 2.34,
            "point_y": 81.15,
            "width": 1.77,
            "height": 3.365,
            "status": "99",
            "status_name": "未知",
        },
    ]
    data = render_seat_map("座位图", "2026-10-21 08:00–23:00", rows, "14:13")
    image = Image.open(io.BytesIO(data)).crop((0, 300, 4096, 2480))

    def bounds(color):
        channels = ImageChops.difference(
            image, Image.new("RGB", image.size, color)
        ).split()
        mask = ImageChops.lighter(
            ImageChops.lighter(channels[0], channels[1]), channels[2]
        ).point(lambda value: 255 if value == 0 else 0)
        return mask.getbbox()

    booked, free, unknown = [bounds(COLORS[s]) for s in ["reserved", "free", "unknown"]]
    assert booked[0] == free[0]
    assert booked[1] == unknown[1]
    assert len({(b[2] - b[0], b[3] - b[1]) for b in [booked, free, unknown]}) == 1


def test_dense_four_digit_seat_numbers_fit_inside_their_color_blocks(monkeypatch):
    from PIL import ImageDraw
    from henu_plugin import seat_map_image
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('seat_preview', Path(__file__).parents[1] / 'ops/render-seat-map-font-preview.py')
    preview = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preview)
    rectangles, labels = [], []
    original_box = ImageDraw.ImageDraw.rounded_rectangle
    from henu_plugin import font_assets
    original_text = font_assets.draw_text
    def box(draw, xy, *args, **kwargs):
        rectangles.append(xy)
        return original_box(draw, xy, *args, **kwargs)
    def text(draw, xy, value, size, fonts, *, weight='regular', **kwargs):
        if str(value).isdigit():
            labels.append(draw.textbbox(xy, str(value), font=fonts(size, weight), anchor=kwargs.get('anchor')))
        return original_text(draw, xy, value, size, fonts, weight=weight, **kwargs)
    monkeypatch.setattr(ImageDraw.ImageDraw, 'rounded_rectangle', box)
    monkeypatch.setattr(font_assets, 'draw_text', text)
    seat_map_image.render_seat_map('座位图', '2026-11-17 08:00–23:00', preview.rows(2000), '12:00')
    assert len(labels) == 2000
    for label, block in zip(labels, rectangles[5:]):
        assert block[0] + 3 <= label[0] < label[2] <= block[2] - 3
        assert block[1] + 3 <= label[1] < label[3] <= block[3] - 3
