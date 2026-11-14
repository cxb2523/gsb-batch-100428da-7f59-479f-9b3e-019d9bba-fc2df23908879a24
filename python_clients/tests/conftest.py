import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Делаем пакеты клиента (coms, client_tools, client_cli) импортируемыми
# без установки, чтобы тесты запускались прямо из папки python_clients.
CLIENTS_DIR = Path(__file__).resolve().parents[1]
if str(CLIENTS_DIR) not in sys.path:
    sys.path.insert(0, str(CLIENTS_DIR))

# Тесты полностью офлайновые и не должны ходить в GitHub.
os.environ.pop('OZON_SERVICE_URL', None)

# Логи и результаты тестов не должны попадать в рабочий каталог results/.
_TEST_RESULTS_DIR = tempfile.mkdtemp(prefix='ozon-test-results-')
os.environ['OZON_RESULTS_DIR'] = _TEST_RESULTS_DIR
atexit.register(shutil.rmtree, _TEST_RESULTS_DIR, True)
