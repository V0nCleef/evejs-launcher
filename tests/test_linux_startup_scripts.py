"""Linux startup scripts preserve literal argument values without shell parsing."""
import json
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='Linux Bash script launcher')


def test_shell_script_arguments_are_literal_and_cwd_is_script_folder(tmp_path):
    from src.core.startup_scripts import ScriptEntry, launch_batch
    script = tmp_path / 'startup with spaces.sh'
    marker = tmp_path / 'should-not-exist'
    output = tmp_path / 'result.json'
    script.write_text('python3 -c \'import json,os,sys;open("result.json","w").write(json.dumps([os.getcwd(),sys.argv[1:]]))\' "$@"\n')
    arguments = f"'$(touch {marker})' 'one;two' 'with spaces'"
    process = launch_batch(ScriptEntry.create('fixture script', str(script), arguments=arguments))
    assert process.wait(timeout=5) == 0
    assert json.loads(output.read_text()) == [str(tmp_path), [f'$(touch {marker})', 'one;two', 'with spaces']]
    assert not marker.exists()
