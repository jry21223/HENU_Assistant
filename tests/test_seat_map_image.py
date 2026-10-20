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
