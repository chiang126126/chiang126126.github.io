# 测试包：在导入 radar 之前把数据目录指到临时目录，避免污染仓库里的 data/
import os
import tempfile

os.environ.setdefault("RADAR_DATA_DIR", tempfile.mkdtemp(prefix="meme-radar-test-"))
os.environ["RADAR_OFFLINE"] = "0"
