#!/usr/bin/env node
/**
 * 假的本地智能体 CLI —— 复刻 WorkBuddy / CodeBuddy 的 stream-json 协议,专供测试。
 *
 * 用 FAKE_MODE 控制行为:ok / auth / empty / fail / text
 * FAKE_DUMP 指向一个文件,会把收到的参数和消息体写进去,方便断言。
 */

import fs from 'node:fs';

const mode = process.env.FAKE_MODE || 'ok';
const dumpPath = process.env.FAKE_DUMP || '';

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { raw += chunk; });
process.stdin.on('end', () => { main(raw); });

function main(input) {
    let payload = null;
    for (const line of input.split('\n')) {
        const text = line.trim();
        if (!text.startsWith('{')) continue;
        try { payload = JSON.parse(text); } catch (e) { /* 忽略 */ }
    }

    if (dumpPath) {
        fs.writeFileSync(dumpPath, JSON.stringify({
            argv: process.argv.slice(2),
            payload: payload,
            cwd: process.cwd(),
            hasImage: hasImage(payload),
        }, null, 2), 'utf8');
    }

    const send = (obj) => process.stdout.write(JSON.stringify(obj) + '\n');

    // 真实 WorkBuddy CLI 未登录时的形态:assistant 里带提示,result 标记 is_error
    if (mode === 'err') {
        const note = 'Authentication required. Please use /login command to sign in to your account';
        send({ type: 'system', subtype: 'init', session_id: 'fake-session' });
        send({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: note }] } });
        send({
            type: 'result',
            subtype: 'error_during_execution',
            is_error: true,
            errors: [note],
            duration_ms: 477,
        });
        process.exit(0);
    }

    if (mode === 'auth') {
        process.stdout.write('Authentication required. Please use /login command to sign in to your account\n');
        process.exit(0);
    }
    if (mode === 'empty') {
        process.exit(0);
    }
    if (mode === 'fail') {
        process.stderr.write('boom: 模拟启动失败\n');
        process.exit(3);
    }
    if (mode === 'text') {
        process.stdout.write('纯文本答案:B\n');
        process.exit(0);
    }

    const answer = '【答案】B\n\n【解析】由 v = s / t 得 36 km/h = 10 m/s。';

    send({ type: 'system', subtype: 'init', session_id: 'fake-session' });
    for (let i = 0; i < answer.length; i += 6) {
        send({
            type: 'stream_event',
            event: { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text: answer.slice(i, i + 6) } },
            session_id: 'fake-session',
        });
    }
    send({ type: 'assistant', message: { role: 'assistant', content: [{ type: 'text', text: answer }] } });
    send({ type: 'result', subtype: 'success', is_error: false, result: answer, duration_ms: 1234 });
    process.exit(0);
}

function hasImage(payload) {
    const content = payload && payload.message && payload.message.content;
    if (!Array.isArray(content)) return false;
    return content.some((part) => part && part.type === 'image');
}