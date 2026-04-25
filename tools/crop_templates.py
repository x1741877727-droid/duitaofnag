#!/usr/bin/env python3
"""
从游戏截图中裁剪 UI 元素，生成模板匹配用的模板图片。
所有截图均为 1280x720 分辨率。
"""

import os
from PIL import Image

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCREENSHOTS_DIR = os.path.join(PROJECT_ROOT, "fixtures", "screenshots")
TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "fixtures", "templates")

# 基准分辨率
BASE_W, BASE_H = 1280, 720


def crop_and_save(src_filename: str, template_name: str, box: tuple[int, int, int, int]):
    """
    从 src_filename 裁剪 box=(left, upper, right, lower) 区域，
    保存到 TEMPLATES_DIR/template_name。
    如果实际分辨率不是 1280x720，按比例缩放 box。
    """
    src_path = os.path.join(SCREENSHOTS_DIR, src_filename)
    dst_path = os.path.join(TEMPLATES_DIR, template_name)

    if not os.path.exists(src_path):
        print(f"  [跳过] 源文件不存在: {src_path}")
        return

    img = Image.open(src_path)
    w, h = img.size
    print(f"  源: {src_filename} ({w}x{h})")

    # 按比例缩放裁剪区域
    sx = w / BASE_W
    sy = h / BASE_H
    scaled_box = (
        int(box[0] * sx),
        int(box[1] * sy),
        int(box[2] * sx),
        int(box[3] * sy),
    )

    # 确保不越界
    scaled_box = (
        max(0, scaled_box[0]),
        max(0, scaled_box[1]),
        min(w, scaled_box[2]),
        min(h, scaled_box[3]),
    )

    cropped = img.crop(scaled_box)
    cropped.save(dst_path)
    cw, ch = cropped.size
    print(f"  -> {template_name}  crop={scaled_box}  size={cw}x{ch}")


def main():
    os.makedirs(TEMPLATES_DIR, exist_ok=True)
    print(f"输出目录: {TEMPLATES_DIR}\n")

    # ---------------------------------------------------------------
    # 1. youxihuodong-7.png — 开局领奖励 popup
    #    小 X 按钮在右上角，金色/白色叉号
    # ---------------------------------------------------------------
    print("[1] youxihuodong-7.png -> close_x_small.png")
    crop_and_save("youxihuodong-7.png", "close_x_small.png",
                  (1200, 40, 1248, 82))

    # ---------------------------------------------------------------
    # 2. youxihuodong-7.1.png — 回归签到 popup
    #    X 按钮在右上角，较大的白色叉号
    # ---------------------------------------------------------------
    print("\n[2] youxihuodong-7.1.png -> close_x_large.png")
    crop_and_save("youxihuodong-7.1.png", "close_x_large.png",
                  (1155, 24, 1205, 64))

    # ---------------------------------------------------------------
    # 3. youxihuodong-7.1.1.png — 新春共创赛 popup
    #    X 按钮在右侧，白色粗叉号
    # ---------------------------------------------------------------
    print("\n[3] youxihuodong-7.1.1.png -> close_x_round.png")
    crop_and_save("youxihuodong-7.1.1.png", "close_x_round.png",
                  (1198, 44, 1240, 94))

    # ---------------------------------------------------------------
    # 4. dating-8.png — 大厅主界面
    #    a) "开始游戏" 红色按钮在左上角
    #    b) "组队" 区域在左侧中下位置（可见 "组队 02" 文字 + 找队友按钮）
    # ---------------------------------------------------------------
    print("\n[4a] dating-8.png -> lobby_start_game.png")
    crop_and_save("dating-8.png", "lobby_start_game.png",
                  (0, 0, 180, 70))

    print("\n[4b] dating-8.png -> lobby_team_button.png")
    crop_and_save("dating-8.png", "lobby_team_button.png",
                  (4, 370, 110, 440))

    # ---------------------------------------------------------------
    # 5. gonggao.png — 公告弹窗
    #    X 关闭按钮在弹窗右上角（灰色叉号在深蓝色背景上）
    # ---------------------------------------------------------------
    print("\n[5] gonggao.png -> popup_close_x_gonggao.png")
    crop_and_save("gonggao.png", "popup_close_x_gonggao.png",
                  (1082, 90, 1140, 134))

    # ---------------------------------------------------------------
    # 6. neicundi-5.1.png — 内存提醒
    #    "确定" 黄色按钮在弹窗中央下方
    # ---------------------------------------------------------------
    print("\n[6] neicundi-5.1.png -> button_confirm.png")
    crop_and_save("neicundi-5.1.png", "button_confirm.png",
                  (530, 470, 750, 520))

    # ---------------------------------------------------------------
    # 7. liuhuaguanbi-2.png — 六花加速器 未连接
    #    ▶ play 圆形按钮在右下角
    # ---------------------------------------------------------------
    print("\n[7] liuhuaguanbi-2.png -> accelerator_play.png")
    crop_and_save("liuhuaguanbi-2.png", "accelerator_play.png",
                  (1145, 615, 1265, 715))

    # ---------------------------------------------------------------
    # 8. liuhuakaiqi-3.png — 六花加速器 已连接
    #    ⏸ pause 圆形按钮在右下角
    # ---------------------------------------------------------------
    print("\n[8] liuhuakaiqi-3.png -> accelerator_pause.png")
    crop_and_save("liuhuakaiqi-3.png", "accelerator_pause.png",
                  (1145, 615, 1265, 715))

    # ---------------------------------------------------------------
    # 9. zuduima-9.1.png — 组队码加入提示
    #    "加入" 黄色按钮在弹窗右侧
    # ---------------------------------------------------------------
    print("\n[9] zuduima-9.1.png -> button_join.png")
    crop_and_save("zuduima-9.1.png", "button_join.png",
                  (680, 460, 900, 515))

    # ---------------------------------------------------------------
    # 10. zhoaduiyou.png — 找队友弹窗
    #     "暂不需要" 绿色按钮在弹窗左侧
    # ---------------------------------------------------------------
    print("\n[10] zhoaduiyou.png -> button_no_thanks.png")
    crop_and_save("zhoaduiyou.png", "button_no_thanks.png",
                  (420, 460, 650, 510))

    print("\n完成! 所有模板已保存到:", TEMPLATES_DIR)

    # 列出生成的模板
    print("\n--- 生成的模板文件 ---")
    for f in sorted(os.listdir(TEMPLATES_DIR)):
        fp = os.path.join(TEMPLATES_DIR, f)
        if os.path.isfile(fp):
            img = Image.open(fp)
            size = os.path.getsize(fp)
            print(f"  {f:40s} {img.size[0]:4d}x{img.size[1]:<4d}  ({size} bytes)")


if __name__ == "__main__":
    main()
