"""Exercise live MCP discovery without printing credentials; save wire evidence."""
import argparse
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

RUNTIME = {
    'runtime_status', 'runtime_execute_workflow', 'runtime_delegate',
    'runtime_memory_put', 'runtime_memory_search', 'runtime_session_search',
    'runtime_skill_save', 'runtime_skill_list', 'runtime_skill_get',
    'runtime_skill_rollback', 'runtime_skill_execute',
}


def probe(url):
    records = []
    headers = {'Content-Type': 'application/json',
               'Accept': 'application/json, text/event-stream'}
    if os.environ.get('AHMED_TOOLBOX_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['AHMED_TOOLBOX_TOKEN']

    def request(method, params=None, notification=False):
        payload = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if not notification:
            payload['id'] = len(records) + 1
        req = urllib.request.Request(url, json.dumps(payload).encode(), headers)
        with urllib.request.urlopen(req, timeout=120) as response:
            raw = response.read()
            result = json.loads(raw) if raw else None
            records.append({'method': method, 'status': response.status,
                            'headers': dict(response.headers), 'response': result})
            if result:
                assert 'error' not in result, result
                return result['result']

    initialized = request('initialize', {
        'protocolVersion': '2025-06-18', 'capabilities': {},
        'clientInfo': {'name': 'ahmed-discovery-acceptance', 'version': '1.0'},
    })
    headers['MCP-Protocol-Version'] = initialized['protocolVersion']
    request('notifications/initialized', notification=True)
    specs = request('tools/list')['tools']
    names = [spec['name'] for spec in specs]
    assert len(names) == len(set(names)), 'duplicate tool names'
    assert RUNTIME <= set(names), RUNTIME - set(names)
    for spec in specs:
        assert spec['inputSchema']['type'] == 'object', spec['name']
    status = request('tools/call', {'name': 'runtime_status', 'arguments': {}})
    assert not status.get('isError'), status
    assert json.loads(status['content'][0]['text'])['status'] == 'ok'
    workflow = request('tools/call', {'name': 'runtime_execute_workflow', 'arguments': {
        'steps': [{'id': 'doctor', 'tool_name': 'reach_doctor', 'arguments': {}}],
        'timeout_seconds': 60,
    }})
    assert not workflow.get('isError'), workflow
    summary = json.loads(workflow['content'][0]['text'])
    assert summary['status'] == 'ok' and summary['ok_count'] == 1, summary
    legacy = request('tools/call', {'name': 'reach_doctor', 'arguments': {}})
    assert not legacy.get('isError'), legacy
    assert isinstance(json.loads(legacy['content'][0]['text']), dict)
    return {'url': url, 'at': datetime.now(timezone.utc).isoformat(),
            'tool_count': len(names), 'runtime_tools': sorted(RUNTIME),
            'workflow_status': summary['status'], 'records': records}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    report = probe(args.url)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}))
