"""Protocol metadata must never become Core action input."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from projectreader.mcp.server import ProjectReaderMcpAdapter, ProjectReaderMcpServer


class ReaderSpy:
    def __init__(self):
        self.calls = []

    def status(self):
        self.calls.append(('status',))
        return {'state': 'open'}

    def search(self, query, *, limit):
        self.calls.append(('search', query, limit))
        return []


class MetadataContractTests(unittest.TestCase):
    def setUp(self):
        self.reader = ReaderSpy()
        self.server = ProjectReaderMcpServer(ProjectReaderMcpAdapter(self.reader))
        self.server.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-06-18'}})
        self.server.handle_message({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    def call(self, params):
        return self.server.handle_message({'jsonrpc': '2.0', 'id': 2,
                                         'method': 'tools/call', 'params': params})

    def test_metadata_absent_and_arguments_optional(self):
        self.assertFalse(self.call({'name': 'repository_info'})['result']['isError'])
        self.assertEqual(self.reader.calls, [('status',)])

    def test_empty_metadata(self):
        self.assertFalse(self.call({'name': 'repository_info', '_meta': {}})['result']['isError'])

    def test_nonempty_metadata_separated_from_arguments(self):
        params = {'name': 'search_repository', 'arguments': {'query': '你好', 'limit': 3},
                  '_meta': {'progressToken': 'opaque-test'}}
        before = copy.deepcopy(params)
        self.assertFalse(self.call(params)['result']['isError'])
        self.assertEqual(params, before)
        self.assertEqual(self.reader.calls, [('search', '你好', 3)])

    def test_multiple_opaque_metadata_values(self):
        response = self.call({'name': 'repository_info', 'arguments': {}, '_meta': {
            'progressToken': 17, 'example.org/context': {'flags': [True, None]},
            'example.org/trace': 'synthetic-only'}})
        self.assertFalse(response['result']['isError'])
        self.assertNotIn('synthetic-only', json.dumps(response))
        self.assertEqual(self.reader.calls, [('status',)])

    def test_malformed_metadata_is_protocol_error_without_dispatch(self):
        for meta in (None, [], '', 1, True):
            with self.subTest(meta=meta):
                response = self.call({'name': 'repository_info', '_meta': meta})
                self.assertEqual(response['error']['code'], -32602)
        self.assertEqual(self.reader.calls, [])

    def test_unknown_business_field_stays_rejected(self):
        response = self.call({'name': 'search_repository', '_meta': {'progressToken': 1},
                              'arguments': {'query': 'hello', 'definitely_unknown_action_field': True}})
        self.assertEqual(response['error']['code'], -32602)
        self.assertEqual(self.reader.calls, [])

    def test_metadata_inside_arguments_is_not_silently_removed(self):
        for field in ('_meta', '_future_private_field'):
            with self.subTest(field=field):
                response = self.call({'name': 'repository_info', 'arguments': {field: {}}})
                self.assertEqual(response['error']['code'], -32602)
        self.assertEqual(self.reader.calls, [])

    def test_unknown_envelope_field_stays_rejected(self):
        response = self.call({'name': 'repository_info', '_future': 1})
        self.assertEqual(response['error']['code'], -32602)
        self.assertEqual(self.reader.calls, [])

    def test_adapter_direct_call_stays_strict(self):
        from projectreader.mcp.server import McpRequestError
        with self.assertRaises(McpRequestError):
            self.server.adapter.call('repository_info', {'_meta': {}})
        self.assertEqual(self.reader.calls, [])


class MetadataStdioTests(unittest.TestCase):
    def test_cold_process_handshake_and_metadata_calls(self):
        # In installed-wheel tests this deliberately resolves site-packages,
        # not the original checkout. No hard-coded machine or fixture paths.
        import projectreader
        environment = dict(os.environ)
        environment['PYTHONPATH'] = str(Path(projectreader.__file__).resolve().parent.parent)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'repo'
            root.mkdir()
            (root / 'greetings.py').write_text('def hello():\n    return "你好"\n', encoding='utf-8')
            messages = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {
                    'protocolVersion': '2025-06-18', 'capabilities': {},
                    'clientInfo': {'name': 'compatibility-fixture', 'version': '1'}}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {
                    'name': 'search_repository', 'arguments': {'query': 'hello'},
                    '_meta': {'progressToken': 3, 'example.org/request': {'kind': 'synthetic'}}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {
                    'name': 'repository_info', 'arguments': {'definitely_unknown_action_field': 1},
                    '_meta': {}}},
                {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {
                    'name': 'repository_info', 'arguments': {}, '_meta': {}}},
            ]
            completed = subprocess.run([sys.executable, '-B', '-m', 'projectreader.mcp', '--repo', str(root)],
                input=('\n'.join(json.dumps(m, ensure_ascii=False) for m in messages)+'\n').encode(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment, cwd=directory, timeout=30)
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(completed.stderr, b'')
            responses = [json.loads(line) for line in completed.stdout.decode('utf-8').splitlines()]
            self.assertEqual([r['id'] for r in responses], [1, 2, 3, 4, 5])
            self.assertEqual(len(responses[1]['result']['tools']), 10)
            self.assertFalse(responses[2]['result']['isError'])
            self.assertEqual(responses[3]['error']['code'], -32602)
            self.assertFalse(responses[4]['result']['isError'])
            self.assertNotIn('unsupported fields: _meta', completed.stdout.decode())


if __name__ == '__main__':
    unittest.main()
