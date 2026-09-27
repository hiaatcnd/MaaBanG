import json
from pathlib import Path
import subprocess
import sys
import unittest


class ChartWorkerBootstrapTests(unittest.TestCase):
    def test_isolated_worker_loads_its_adjacent_modules(self):
        root=Path(__file__).resolve().parents[1]
        worker=root/'agent/chart_worker.py'
        code=(f'import runpy,json;runpy.run_path({str(worker)!r},run_name="bootstrap_test");'
              'import chart_sync,chart_timing;print(json.dumps([chart_sync.__file__,chart_timing.__file__]))')
        result=subprocess.run([sys.executable,'-I','-c',code],capture_output=True,
                              text=True,encoding='utf8',check=True,timeout=15)
        paths=json.loads(result.stdout)
        self.assertEqual([Path(p).resolve().parent for p in paths],[root/'agent']*2)


if __name__=='__main__':
    unittest.main()
