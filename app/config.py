from pathlib import Path
import os
from app.rule_registry import CURRENT_RULE_VERSION
ROOT=Path(__file__).resolve().parent.parent
DATA_DIR=Path(os.environ.get("DATA_DIR", "/tmp/transitcap"))
TITLE='公交刷卡换乘优惠与日封顶回算'
SHORT='transitcap'
COLUMNS=['event_id', 'card', 'route', 'event_time', 'result', 'reported', 'note']
MODULES=[('taps', '刷卡采信'), ('routes', '线路换乘'), ('fares', '票价版本'), ('journeys', '行程配对'), ('discounts', '优惠回算'), ('clearing', '清分对账'), ('settlement', '日结归档')]
# 当前生效规则版本统一取自规则登记簿，避免散落常量漂移。
RULE_VERSION=CURRENT_RULE_VERSION
