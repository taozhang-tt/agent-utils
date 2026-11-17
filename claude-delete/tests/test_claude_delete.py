import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load('install')
delete = load('delete_session')


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'config with spaces'
        self.root.mkdir()

    def install(self, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            installer.install(self.root, **kwargs)

    def test_install_reinstall_uninstall_preserves_other_hooks(self):
        original = {'model': 'test', 'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo stop'}]}],
                    'UserPromptSubmit': [{'hooks': [{'type': 'command', 'command': 'echo user'}]}]}}
        settings = self.root / 'settings.json'
        settings.write_text(json.dumps(original))
        self.install()
        first = settings.read_bytes()
        self.install()
        self.assertEqual(first, settings.read_bytes())
        config = json.loads(first)
        self.assertEqual(len(config['hooks']['UserPromptSubmit']), 2)
        self.assertEqual(config['hooks']['Stop'], original['hooks']['Stop'])
        backups = list((self.root / 'claude-delete-backups').glob('*/settings.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(json.loads(backups[0].read_text()), original)
        self.install(uninstall=True)
        self.assertEqual(json.loads(settings.read_text()), original)
        self.assertFalse((self.root / 'hooks/delete_session.py').exists())

    def test_conflicting_command_requires_force_and_is_backed_up(self):
        command = self.root / 'commands/delete.md'
        command.parent.mkdir()
        command.write_text('user custom command')
        with self.assertRaises(ValueError):
            self.install()
        self.assertFalse((self.root / 'settings.json').exists())
        self.install(force=True)
        backups = list((self.root / 'claude-delete-backups').glob('*/commands/delete.md'))
        self.assertEqual(backups[0].read_text(), 'user custom command')

    def test_invalid_settings_are_not_overwritten(self):
        settings = self.root / 'settings.json'
        settings.write_text('{broken')
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(settings.read_text(), '{broken')

    def test_installed_hook_with_spaces_and_ordinary_prompt(self):
        self.install()
        settings = json.loads((self.root / 'settings.json').read_text())
        command = settings['hooks']['UserPromptSubmit'][0]['hooks'][0]['command']
        result = subprocess.run(command, shell=True, input=json.dumps({'prompt': 'explain /delete'}),
                                text=True, capture_output=True)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '', ''))

    def test_cleanup_preserves_other_sessions_and_malformed_history(self):
        sid, other = str(uuid.uuid4()), str(uuid.uuid4())
        p = self.root / 'projects/project' / (sid + '.jsonl')
        p.parent.mkdir(parents=True)
        p.write_text('target')
        p.with_suffix('').mkdir()
        (p.with_suffix('') / 'attachment').write_text('data')
        other_file = p.with_name(other + '.jsonl')
        other_file.write_text('keep')
        history = self.root / 'history.jsonl'
        keep = json.dumps({'sessionId': other}) + '\ninvalid\n'
        history.write_text(json.dumps({'sessionId': sid}) + '\n' + keep)
        index = p.parent / 'sessions-index.json'
        index.write_text(json.dumps({'entries': [{'sessionId': sid}, {'sessionId': other}]}))
        for name in ('tasks', 'file-history', 'session-env'):
            (self.root / name / sid).mkdir(parents=True)
        delete.cleanup(self.root, p, sid)
        self.assertFalse(p.exists())
        self.assertFalse(p.with_suffix('').exists())
        self.assertEqual(other_file.read_text(), 'keep')
        self.assertEqual(history.read_text(), keep)
        self.assertEqual(json.loads(index.read_text())['entries'], [{'sessionId': other}])
        for name in ('tasks', 'file-history', 'session-env'):
            self.assertFalse((self.root / name / sid).exists())

    def test_path_mismatch_is_rejected(self):
        sid = str(uuid.uuid4())
        for path in (self.root / (sid + '.jsonl'), self.root / 'projects/project/wrong.jsonl'):
            with self.assertRaises(ValueError):
                delete.validate(self.root, path, sid)

    def test_worker_waits_until_process_exits(self):
        sid = str(uuid.uuid4())
        path = self.root / 'projects/project' / (sid + '.jsonl')
        path.parent.mkdir(parents=True)
        path.write_text('target')
        job = dict(pid=12345, start='same', sid=sid, root=str(self.root), transcript=str(path))
        with patch.object(delete, 'process', side_effect=[(1, 'S', 'same', 'claude'),
                          (1, 'S', 'same', 'claude'), None]), \
                patch.object(delete.os, 'kill') as kill, patch.object(delete.time, 'sleep') as sleep, \
                contextlib.redirect_stdout(io.StringIO()):
            delete.worker(job)
        kill.assert_called_once_with(12345, delete.signal.SIGTERM)
        self.assertFalse(path.exists())
        self.assertEqual(sleep.call_count, 2)

    def test_worker_timeout_preserves_transcript(self):
        with patch.object(delete, 'process', return_value=(1, 'S', 'same', 'claude')), \
                patch.object(delete.os, 'kill'), patch.object(delete.time, 'sleep'), \
                patch.object(delete, 'cleanup') as cleanup:
            with self.assertRaises(RuntimeError):
                delete.worker(dict(pid=12345, start='same'))
        cleanup.assert_not_called()


if __name__ == '__main__':
    unittest.main()
