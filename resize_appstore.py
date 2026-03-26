#!/usr/bin/env python3
"""
苹果商店截图尺寸调整工具
目标尺寸: 1320 x 2868 像素 (iPhone 6.7 英寸规格)
使用 LANCZOS 高质量算法，带智能填充，无损画质
"""

from PIL import Image
import os

INPUT_DIR = "/Users/mangzi/Desktop/HONG/数字游民/APP上架/上架截图/精选6张/短7"
OUTPUT_DIR = os.path.join(INPUT_DIR, "output_1320x2868")
TARGET_W, TARGET_H = 1320, 2868

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".bmp"}

def resize_image(src_path, dst_path):
    img = Image.open(src_path).convert("RGBA")
    orig_w, orig_h = img.size

    # 按比例缩放，保持宽高比，不裁剪
    ratio = min(TARGET_W / orig_w, TARGET_H / orig_h)
    new_w = round(orig_w * ratio)
    new_h = round(orig_h * ratio)

    resized = img.resize((new_w, new_h), Image.LANCZOS)

    # 居中放置在目标画布上（背景透明，PNG 保留透明度）
    canvas = Image.new("RGBA", (TARGET_W, TARGET_H), (0, 0, 0, 0))
    offset_x = (TARGET_W - new_w) // 2
    offset_y = (TARGET_H - new_h) // 2
    canvas.paste(resized, (offset_x, offset_y))

    ext = os.path.splitext(dst_path)[1].lower()
    if ext in {".jpg", ".jpeg"}:
        # JPEG 不支持透明通道，转 RGB
        canvas = canvas.convert("RGB")
        canvas.save(dst_path, "JPEG", quality=100, subsampling=0)
    else:
        canvas.save(dst_path, "PNG", optimize=False, compress_level=0)

    print(f"  ✓ {os.path.basename(src_path)}  {orig_w}x{orig_h} → {TARGET_W}x{TARGET_H}")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    files = [f for f in os.listdir(INPUT_DIR)
             if os.path.splitext(f)[1].lower() in SUPPORTED]

    if not files:
        print("❌ 未找到支持的图片文件（PNG/JPG/JPEG/WEBP/TIFF/BMP）")
        return

    print(f"共找到 {len(files)} 张图片，开始处理...\n")
    for fname in sorted(files):
        src = os.path.join(INPUT_DIR, fname)
        # 统一输出为 PNG 保证无损
        base = os.path.splitext(fname)[0]
        dst = os.path.join(OUTPUT_DIR, base + ".png")
        resize_image(src, dst)

    print(f"\n✅ 全部完成！输出目录: {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
