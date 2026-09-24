// Copyright (c) 2026 Luke Repko
// SPDX-License-Identifier: GPL-3.0-or-later

import {describe,it,expect} from 'vitest';
import {parseReference,referenceAt,number,firmwareStartBlock} from './types';
describe('reference data',()=>{
 it('imports points and interpolates only in range',()=>{const p=parseReference('ah,voltage_v\n0,13.4\n10,12.8\n20,10');expect(referenceAt(p,5)).toBeCloseTo(13.1);expect(referenceAt(p,20)).toBe(10);expect(referenceAt(p,21)).toBeNull();expect(referenceAt(p,-1)).toBeNull();});
 it('rejects duplicate, unordered, missing and nonfinite values',()=>{for(const s of ['0,13\n0,12','1,13\n0,12','0,13\n,12','0,13\n1,NaN'])expect(()=>parseReference(s)).toThrow();});
 it('does not make up a reference when only a rating is known',()=>{expect(parseReference('')).toEqual([]);expect(referenceAt([],10)).toBeNull();});
 it('keeps missing readings distinct from zero',()=>{expect(number({voltage_v:'',seq:1} as any,'voltage_v')).toBeNull();expect(number({voltage_v:'0.0000',seq:1} as any,'voltage_v')).toBe(0);});
});

describe('start compatibility',()=>{
 it('explains why legacy firmware cannot arm even though readings work',()=>{expect(firmwareStartBlock({mode:'serial',protocol:0})).toContain('Firmware update required');});
 it('allows identified CellTrace firmware and simulated tests',()=>{expect(firmwareStartBlock({mode:'serial',protocol:1})).toBeNull();expect(firmwareStartBlock({mode:'demo',protocol:1})).toBeNull();});
 it('keeps replay read-only',()=>{expect(firmwareStartBlock({mode:'replay',protocol:1})).toContain('read-only');});
});
