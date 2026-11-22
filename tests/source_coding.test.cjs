const {test} = require('node:test');
const assert = require('node:assert/strict');
const {points, normalizeText, sameRange, segments, fingerprint} = require('../static/source_coding.js');

test('selection positions use Unicode codepoints and canonical newlines', () => {
  const text = normalizeText('甲😀\r\n乙\r丙');
  assert.equal(text, '甲😀\n乙\n丙');
  assert.equal(points(text).length, 6);
  assert.equal(points(text).slice(1, 4).join(''), '😀\n乙');
  assert.equal(sameRange({start: 1, end: 4}, {start: 1, end: 4, text: 'different preview'}), true);
  assert.equal(sameRange({start: 1, end: 4}, {start: 1, end: 5}), false);
});

test('overlapping highlights keep every source character exactly once', () => {
  const pieces = segments('甲😀乙丙丁戊', 10, [
    {id: 1, start: 9, end: 13}, {id: 2, start: 12, end: 15}, {id: 3, start: 13, end: 14}
  ]);
  assert.equal(pieces.map(piece => piece.text).join(''), '甲😀乙丙丁戊');
  assert.deepEqual(pieces, [
    {text: '甲😀', ids: [1]}, {text: '乙', ids: [1, 2]},
    {text: '丙', ids: [2, 3]}, {text: '丁', ids: [2]}, {text: '戊', ids: []}
  ]);
});

test('adjacent highlights do not bleed into the next annotation', () => {
  assert.deepEqual(segments('甲乙丙丁', 0, [
    {id: 4, start: 0, end: 2}, {id: 7, start: 2, end: 4}, {id: 9, start: 9, end: 10}
  ]), [{text: '甲乙', ids: [4]}, {text: '丙丁', ids: [7]}]);
  assert.deepEqual(segments('', 0, []), []);
});

test('retry fingerprint ignores label order but notices meaningful edits', () => {
  const value = {start: 0, end: 4, text: '甲\r\n乙', labels: [8, 3], secondary: [], note: ''};
  assert.equal(fingerprint(value), fingerprint({...value, text: '甲\n乙', labels: [3, 8]}));
  assert.notEqual(fingerprint(value), fingerprint({...value, note: '需核对'}));
  assert.notEqual(fingerprint(value), fingerprint({...value, uncertain: true}));
  assert.notEqual(fingerprint(value), fingerprint({...value, no_code: true}));
  assert.notEqual(fingerprint(value), fingerprint({...value, end: 3}));
  const withCode = {...value, new_code: {name: '核对资料', definition: ''}};
  assert.notEqual(fingerprint(value), fingerprint(withCode));
  assert.notEqual(fingerprint(withCode), fingerprint({...withCode, new_code: {name: '核对资料', definition: '对照来源'}}));
});

test('image regions with the same text offsets remain distinct', () => {
  const first = {start: 0, end: 0, text: '', region: {x: 0, y: 0, width: 1, height: 1}};
  const second = {...first, region: {...first.region, width: 0.5}};
  assert.equal(sameRange(first, first), true);
  assert.equal(sameRange(first, second), false);
  assert.equal(sameRange(first, {...first, region: null}), false);
  assert.notEqual(fingerprint(first), fingerprint(second));
});
