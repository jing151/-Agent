"""Run the packaged tests with networking disabled; persist logs below examples/."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
def main(output):
    output=output.resolve();output.mkdir(parents=True,exist_ok=False)
    command=[sys.executable,'-X','utf8',str(ROOT/'scripts/offline_python.py'),'--audit-report',str(output/'network.json'),
        '--module','pytest','--','-q','tests','--artifacts-dir',str(output),'--junitxml='+str(output/'pytest.xml')]
    with (output/'pytest.txt').open('x',encoding='utf-8') as stream:
        result=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    if not (output/'summary.json').exists():
        print(json.dumps(dict(status='pytest_start_failed',exit_code=result.returncode,log=str(output/'pytest.txt')),ensure_ascii=False))
        return result.returncode or 1
    summary=json.loads((output/'summary.json').read_text(encoding='utf-8'))
    audit=json.loads((output/'network.json').read_text(encoding='utf-8'))
    print(json.dumps(dict(passed=summary['passed'],failed=summary['failed'],exit_code=result.returncode,
        external_network_attempts=audit['external_network_attempts'],new_real_api_calls=0,output=str(output)),ensure_ascii=False))
    return result.returncode

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=ROOT/'examples/test-runs'/('run-'+uuid4().hex[:8]))
    raise SystemExit(main(parser.parse_args().output_dir))
