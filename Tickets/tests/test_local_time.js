// Run with: node tests/test_local_time.js
// Execute the actual template script with lightweight DOM stand-ins.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../templates/base.html'), 'utf8');
const script = [...template.matchAll(/<script>([\s\S]*?)<\/script>/g)]
    .map(match => match[1]).find(source => source.includes('time[datetime]'));
assert.ok(script, 'Local-time script exists');
const originalTimezone = process.env.TZ;

try {
    for (const [zone, iso, expected] of [
        ['UTC', '2026-01-01T00:30:00Z', '01/01/2026, 00:30'],
        ['America/New_York', '2026-01-01T00:30:00Z', '31/12/2025, 19:30'],
        ['America/New_York', '2026-07-01T12:00:00Z', '01/07/2026, 08:00'],
        ['Asia/Kolkata', '2026-01-01T00:30:00Z', '01/01/2026, 06:00'],
    ]) {
        process.env.TZ = zone;
        const tags = [iso, '', 'invalid'].map(value => ({
            textContent: 'UTC fallback',
            getAttribute: () => value,
            setAttribute(name, content) { this[name] = content; },
        }));
        vm.runInNewContext(script, {
            Date,
            document: {
                addEventListener(event, callback) {
                    assert.equal(event, 'DOMContentLoaded');
                    callback();
                },
                querySelectorAll(selector) {
                    assert.equal(selector, 'time[datetime]');
                    return tags;
                },
            },
        });
        assert.equal(tags[0].textContent, expected);
        assert.ok(tags[0].title.includes(iso));
        assert.equal(tags[1].textContent, 'UTC fallback');
        assert.equal(tags[2].textContent, 'UTC fallback');
        console.log(`PASS: ${zone} ${iso} -> ${expected}`);
    }
} finally {
    if (originalTimezone === undefined) delete process.env.TZ;
    else process.env.TZ = originalTimezone;
}
