"""main.py — 人口移動データを取得してCSVを更新し、ダッシュボードを再生成する。"""
import os

from dotenv import load_dotenv

import migration_tracker
import visualize

if __name__ == "__main__":
    load_dotenv()
    migration_tracker.main(os.environ["E_STAT_APP_ID"])
    visualize.main()
